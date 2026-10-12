"""One explicit research intake step, with no automatic repair or redispatch."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import time
import uuid

from scisaurus.core.errors import StateError, ValidationError
from scisaurus.core.schema import canonical_bytes
from scisaurus.runtime.development_session import DevelopmentSession, development_session_binding
from scisaurus.runtime.dsh_batch import validate_batch_config, verify_batch_runtime
from scisaurus.runtime.laboratory import LaboratoryBinding, load_attestation
from scisaurus.runtime.program_sandbox import sandbox_read_grants, require_isolated_read_grants
from scisaurus.runtime.run_control import control_lock, read_control, save_control
from scisaurus.runtime.software_workbench import SoftwareWorkbench



from scisaurus.runtime.primary_client import PrimaryClient as IntakePrimaryClient


def sha(body):
    return hashlib.sha256(body).hexdigest()


def preflight(root, runtime_python, *, continuation=None):
    root = Path(root).resolve()
    pointer = json.loads((root / "preparation.json").read_bytes())
    checkpoint_path = Path(pointer["checkpoint_path"]).resolve()
    if not checkpoint_path.is_relative_to(root / "checkpoints"):
        raise StateError("intake checkpoint has a foreign owner")
    raw = checkpoint_path.read_bytes()
    if sha(raw) != pointer["checkpoint_sha256"] or checkpoint_path.stem != sha(raw):
        raise StateError("intake checkpoint changed")
    checkpoint = json.loads(raw)
    project = root / "composer"
    if checkpoint["mission_id"] != root.name or checkpoint["development_session"] != development_session_binding(project):
        raise StateError("intake belongs to another project")
    ticket = None
    if continuation is not None:
        ticket_path = Path(continuation).resolve()
        ticket_raw = ticket_path.read_bytes()
        ticket = json.loads(ticket_raw)
        if (not ticket_path.is_relative_to(project / "intake-controller/recoveries")
                or ticket_path.stem != sha(ticket_raw)
                or ticket.get("schema_version") != "explicit-intake-continuation-1"
                or ticket.get("preparation_sha256") != sha(raw)
                or ticket.get("session_id") != checkpoint["session_id"]
                or ticket.get("development_session") != checkpoint["development_session"]
                or sha((project / "output/run-control.json").read_bytes()) != ticket["control_sha256"]
                or sha(Path(ticket["backend_binding"]).read_bytes()) != ticket["backend_binding_sha256"]):
            raise StateError("intake continuation lost its sealed owner or stopped control")
    for name, expected in checkpoint["input_sha256"].items():
        if ticket and name == "composer/output/run-control.json":
            continue
        path = root / name
        if not path.resolve().is_relative_to(root) or path.is_symlink() or sha(path.read_bytes()) != expected:
            raise StateError("sealed intake input changed: " + name)
    state_raw = (project / "development-session/session.json").read_bytes()
    state = json.loads(state_raw)
    expected_state = ticket["session_sha256"] if ticket else checkpoint["session_initial_sha256"]
    if (sha(state_raw) != expected_state or state["session_id"] != checkpoint["session_id"]
            or state["binding"] != checkpoint["development_session"] or state["active_job"]
            or not ticket and state["calls"]):
        raise StateError("first intake step requires its unused reserved session")
    backend = validate_batch_config(ticket["backend"] if ticket else json.loads((root / "backend.json").read_bytes()))
    verify_batch_runtime(backend)
    if ticket and ticket.get('step_request', {}).get('kind') in {'concept_continuation','research_work'}:
        from scisaurus.runtime.intake_guard import guarded_backend
        guarded_backend(backend)
    if state["config_sha256"] != sha(canonical_bytes(backend)):
        raise StateError("intake backend differs from its reserved session")
    if ticket:
        with DevelopmentSession(checkpoint["development_session"]).lease(backend, time.monotonic() + 2) as session:
            if (not session.state["calls"] or session.state["calls"][-1]["sha256"] != ticket["receipt_sha256"]
                    or (ticket.get("prior_receipt_status", "result_unknown") == "result_unknown"
                        and not session._reconciled_unknown(session.state["calls"][-1], json.loads(
                            Path(session.state["calls"][-1]["receipt"]).read_bytes())))):
                raise StateError("intake continuation requires its exact accounted unknown")
    if ticket:
        previous_receipt = json.loads(Path(state["calls"][-1]["receipt"]).read_bytes())
        if previous_receipt.get("status") != ticket.get("prior_receipt_status", "result_unknown"):
            raise StateError("intake continuation changed its predecessor terminal status")
    if ticket and ticket.get('step_request',{}).get('kind')=='research_work':
        from scisaurus.runtime.primary_work import work_request
        from scisaurus.runtime.intake_recovery import stopped_step
        with stopped_step(root,receipt_sha256=ticket['receipt_sha256'],receipt_status=ticket['prior_receipt_status'],
                attempt_path=read_control(project/'output')['attempt']) as context:
            if ticket['step_request']!=work_request(context,ticket['step_request'].get('feedback')):
                raise StateError('research checkpoint evidence changed')
    if not Path(runtime_python).is_file():
        raise StateError("intake requires an installed Python runtime")
    assignment = json.loads((root / "inputs/assignment.json").read_bytes())
    lab = LaboratoryBinding.load(checkpoint["laboratory_config_path"])
    lab.attestation = load_attestation(checkpoint["laboratory_attestation_path"])
    expected = assignment["laboratory"]
    if (lab.identity != expected["config_identity"]
            or lab.attestation["config_sha256"] != lab.identity
            or lab.attestation["attestation_sha256"] != expected["attestation_identity"]):
        raise StateError("intake laboratory differs from its sealed configuration or attestation")
    protected = [path for path in root.parent.iterdir() if path.is_dir() and path.resolve() != root]
    job = project / "intake-jobs/preflight"
    grants, files = sandbox_read_grants(job / "work", backend["command"],
        read_only_paths=[*backend.get("read_roots", []), str(Path(runtime_python).resolve().parent.parent)])
    require_isolated_read_grants(grants, files, isolated_from=protected, admitted_root=job)
    return root, checkpoint, backend, assignment, protected


def strict_backend(root, checkpoint, backend, runtime_python):
    """Bind the reserved primary to the shared native runtime without a dispatch."""
    from scisaurus.runtime.primary_client import prepare_primary_backend
    active=prepare_primary_backend(backend,root/'composer/intake-controller',runtime_python,
        checkpoint['development_session'])
    state=json.loads(Path(checkpoint['development_session']['root'],'session.json').read_bytes())
    return active,Path(state['pre_dispatch_binding']['path'])


def recovery_evidence(value):
    if (not isinstance(value, dict) or set(value) != {"source", "observed_at"}
            or not isinstance(value["source"], str) or not value["source"].strip()):
        raise StateError("intake requires externally confirmed account capacity and its observation time")
    try:
        observed = datetime.fromisoformat(value["observed_at"].replace("Z", "+00:00"))
        if observed.tzinfo is None or observed > datetime.now(timezone.utc):
            raise ValueError("observation is not a past timezone-aware time")
    except (ValueError, TypeError, AttributeError) as error:
        raise StateError("capacity recovery needs an actual past timezone-aware observation") from error
    return deepcopy(value)


def validate_proposal(value, assignment):
    contract = assignment["output_contract"]["topic_selection"]
    if (not isinstance(value, dict) or set(value) != set(contract)
            or value["decision"] not in {"propose", "abstain"}
            or not isinstance(value["candidates"], list)
            or not isinstance(value["selection_rationale"], str) or not value["selection_rationale"].strip()
            or not isinstance(value["limitations"], list)
            or any(not isinstance(x, str) for x in value["limitations"])):
        raise ValidationError("topic selection differs from its required proposal contract")
    ids = []
    excluded = {" ".join(x["research_question"].casefold().split()) for x in assignment["excluded_topics"]}
    for candidate in value["candidates"]:
        if (not isinstance(candidate, dict) or set(candidate) != set(contract["candidates"][0])
                or any(not isinstance(x, str) or not x.strip() for x in candidate.values())):
            raise ValidationError("topic candidate requires every declared comparison field")
        if " ".join(candidate["question"].casefold().split()) in excluded:
            raise ValidationError("topic candidate repeats an excluded research question")
        ids.append(candidate["id"])
    if (len(set(ids)) != len(ids)
            or value["decision"] == "propose" and value["selected_candidate_id"] not in ids
            or value["decision"] == "abstain" and value["selected_candidate_id"] is not None):
        raise ValidationError("topic selection has an inconsistent candidate identity")


def run_once(root, runtime_python, *, recovery=None, continuation=None):
    recovery = recovery_evidence(recovery) if recovery is not None else None
    root, checkpoint, backend, assignment, protected = preflight(root, runtime_python, continuation=continuation)
    if continuation:
        ticket = json.loads(Path(continuation).read_bytes())
        assignment["transport_feedback"] = ticket["feedback"]
        state=json.loads((root/'composer/development-session/session.json').read_bytes())
        from scisaurus.runtime.requested_review import receipt_has_provider_hold
        if recovery is None and receipt_has_provider_hold(state['calls'][-1]['receipt']):
            raise StateError('provider quota recovery requires external capacity confirmation')
    elif recovery is None:
        raise StateError('initial dispatch requires externally confirmed account capacity')
    from scisaurus.runtime.primary_work import work_assignment
    request=ticket.get('step_request') if continuation else {'kind':'research_work','checkpoint':{}}
    assignment=work_assignment(assignment,request)
    project = root / "composer"
    output = project / "output"
    directory = project / "intake-controller"
    directory.mkdir(exist_ok=True)
    attempt = (Path(continuation).with_suffix(".attempt.json") if continuation else directory / "attempt.json")
    generation = uuid.uuid4().hex
    deadline = time.monotonic() + backend["timeout_seconds"]
    # The marker and launch permission are acquired before any provider request.
    with control_lock(output):
        if attempt.exists():
            raise StateError("intake attempt already exists; no automatic retry is permitted")
        control = read_control(output)
        if control.get("stop_requested") is not True or not continuation and control.get("generation"):
            raise StateError("intake requires its stopped, ungranted control")
        record = {"schema_version": "fresh-intake-attempt-1", "mission_id": root.name,
            "session_id": checkpoint["session_id"], "preparation_sha256": sha(canonical_bytes(checkpoint)),
            "recovery": recovery, "status": "started", "scientific_admission": "not_assessed",
            "receipt": None, "usage": None}
        attempt.write_bytes(canonical_bytes(record))
        save_control(output, {**control, "generation": generation, "stop_requested": False,
                              "controller": "primary-work", "attempt": str(attempt)})
    names = ("SCISAURUS_RUN_CONTROL", "SCISAURUS_RUN_GENERATION")
    previous = {key: os.environ.get(key) for key in names}
    os.environ.update(dict(zip(names, (str(output / "run-control.json"), generation))))
    try:
        if continuation:
            binding = ticket["backend_binding"]
        else:
            backend, binding = strict_backend(root, checkpoint, backend, runtime_python)
        record["backend_binding"] = str(binding)
        client = IntakePrimaryClient(backend, root=project / "intake-jobs",
            runtime_python=runtime_python, development_session=checkpoint["development_session"],
            isolated_from=protected,
            forbid_reasoning=bool(continuation))
        client.deadline = deadline
        client.runner.task_contract={**client.runner.task_contract,"mode":"research_work","input_delivery":"inline",
            "native_controller_tools":True}
        from scisaurus.runtime.primary_work import native_tool_handler
        lab=LaboratoryBinding.load(checkpoint['laboratory_config_path'])
        lab.attestation=load_attestation(checkpoint['laboratory_attestation_path'])
        assignment['available_open_source_tools']=lab.context()
        from scisaurus.runtime.requested_review import request_review,retain_review_dispatch
        record['review_dispatches']=deepcopy(request.get('checkpoint',{}).get('review_dispatches',[]))
        def review(args):
            def retain(dispatch):
                retain_review_dispatch(record,dispatch)
                attempt.write_bytes(canonical_bytes(record))
            return request_review({'author_backend':backend,'runtime_python':runtime_python},
                project/'independent-reviews',args,deadline=deadline,isolated_from=[project],record=retain,laboratory=lab)
        client.runner.native_tool_handler=native_tool_handler(assignment,
            SoftwareWorkbench(project/'scientific-software',deadline=deadline,laboratory=lab,
                history_refs=[x['receipt_ref'] for x in request.get('checkpoint',{}).get('observations',[]) if x.get('receipt_ref')]),record,review=review)
        system="The primary owns research decisions. Tools return actual observations and errors. Replies and checkpoints are recorded automatically. Retained inputs and history are evidence."
        result = client.complete(system=system,prompt=json.dumps(assignment))
        record.update(receipt=result.response_metadata["receipt"], usage=result.usage)
        response = result.json_object()
        record["response"] = response
        from scisaurus.runtime.primary_work import review_feedback
        from scisaurus.runtime.input_delivery import input_read_coverage
        record['input_read_coverage']=input_read_coverage(record['receipt'])
        record['review_feedback']=review_feedback(response)
        record['status']='stopped_with_research_progress'
    except BaseException as error:
        record.update(status="stopped_on_error", error=f"{type(error).__name__}: {error}")
        if getattr(error, 'input_read_coverage', None) is not None:
            record['input_read_coverage']=error.input_read_coverage
        if getattr(error, "receipt", None):
            record["receipt"] = str(error.receipt)
        if getattr(error, "usage", None) is not None:
            record["usage"] = error.usage
        record["failure"] = error.failure_details() if callable(getattr(error, "failure_details", None)) else None
        raise
    finally:
        try:
            state = json.loads((project / "development-session/session.json").read_bytes())
            calls = state.get("calls", [])
            if record["receipt"] is None and (state.get("active_job") or calls):
                path = Path(state["active_job"] if state.get("active_job") else calls[-1]["receipt"])
                raw = path.read_bytes()
                if (not path.resolve().is_relative_to(project / "intake-jobs")
                        or not state.get("active_job") and sha(raw) != calls[-1]["sha256"]):
                    raise StateError("intake lost its owned paid receipt")
                receipt = json.loads(raw)
                if (receipt.get("session_id") != checkpoint["session_id"]
                        or receipt.get("development_session", {}).get("binding") != checkpoint["development_session"]
                        or receipt.get("config_sha256") != sha(canonical_bytes(backend))
                        or Path(receipt.get("job_directory", "")).resolve() != path.parent.resolve()):
                    raise StateError("intake paid receipt belongs to another session or backend")
                record.update(receipt=str(path), receipt_sha256=sha(raw),
                              usage=receipt.get("usage"), receipt_status=receipt.get("status"))
            body = canonical_bytes(record)
            sealed = directory / (sha(body) + ".json")
            sealed.write_bytes(body)
            sealed.chmod(0o444)
            attempt.write_bytes(body)
        finally:
            try:
                with control_lock(output):
                    current = read_control(output)
                    if current.get("generation") == generation:
                        save_control(output, {**current, "stop_requested": True,
                            "research_resume_authorization_ref": None})
            finally:
                for key, value in previous.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value
    return record
