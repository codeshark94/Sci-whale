"""Explicit accounting and transport repair for a stopped primary intake."""
from copy import deepcopy
import json
from pathlib import Path

from scisaurus.core.errors import StateError
from scisaurus.core.events import ControlStore
from scisaurus.core.store import ArtifactStore
from scisaurus.core.schema import canonical_bytes
from scisaurus.runtime.development_session import DevelopmentSession
from scisaurus.runtime.dsh_batch import validate_batch_config, verify_batch_runtime
from scisaurus.runtime.fresh_intake import sha
from scisaurus.runtime.run_control import control_lock, read_control


def reasoning_off_composition(composition, provider, model):
    """Use the endpoint's OpenAI reasoning effort field, including explicit none."""
    value = deepcopy(composition)
    matches = [x["config"]["providers"][provider] for x in value
               if provider in x.get("config", {}).get("providers", {})]
    if len(matches) != 1:
        raise StateError("reasoning repair requires one exact provider profile")
    profile = matches[0]
    if profile.get("api") != "openai-completions" or profile.get("reasoning") != "off":
        raise StateError("reasoning repair requires an explicit off OpenAI-compatible route")
    models = [m for m in profile["models"] if m["id"] == model]
    if len(models) != 1:
        raise StateError("reasoning repair lost its fixed model")
    profile.setdefault("compat", {}).update(thinkingFormat="openai", supportsReasoningEffort=True)
    models[0]["reasoningEfforts"]["off"] = "none"
    return value


def prepare_continuation(root, *, receipt_sha256, attempt_path=None):
    """Account a reaped unknown and seal one explicit next step without provider access."""
    if (not isinstance(receipt_sha256, str) or len(receipt_sha256) != 64
            or any(c not in "0123456789abcdef" for c in receipt_sha256)):
        raise StateError("intake recovery requires an exact SHA256 receipt identity")
    root = Path(root).resolve()
    project = root / "composer"
    pointer = json.loads((root / "preparation.json").read_bytes())
    checkpoint_raw = Path(pointer["checkpoint_path"]).read_bytes()
    if sha(checkpoint_raw) != pointer["checkpoint_sha256"]:
        raise StateError("intake recovery lost its immutable preparation")
    checkpoint = json.loads(checkpoint_raw)
    session = DevelopmentSession(checkpoint["development_session"])
    if Path(session.binding["project_id"]) != project:
        raise StateError("intake recovery has a foreign session")
    directory = project / "intake-controller/recoveries" / receipt_sha256
    if directory.exists():
        tickets = []
        for path in directory.glob("*.json"):
            value = json.loads(path.read_bytes())
            if isinstance(value, dict) and value.get("schema_version") == "explicit-intake-continuation-1":
                tickets.append(path)
        if tickets:
            if len(tickets) != 1 or tickets[0].with_suffix(".attempt.json").exists():
                raise StateError("explicit recovery already consumed; execution remains stopped")
            ticket = json.loads(tickets[0].read_bytes())
            if tickets[0].stem != sha(tickets[0].read_bytes()):
                raise StateError("intake recovery ticket changed")
            verify_batch_runtime(ticket["backend"])
            with control_lock(project / "output"):
                if (read_control(project / "output").get("stop_requested") is not True
                        or sha((project / "output/run-control.json").read_bytes()) != ticket["control_sha256"]):
                    raise StateError("intake recovery lost stopped execution")
                proof_raw = Path(ticket["backend_binding"]).read_bytes()
                if sha(proof_raw) != ticket["backend_binding_sha256"]:
                    raise StateError("intake recovery backend proof changed")
                proof = json.loads(proof_raw)
                if sha(session.path.read_bytes()) == proof["previous_session_sha256"]:
                    next_state = json.loads(session.path.read_bytes())
                    next_state.setdefault("backend_revisions", []).append({
                        "path": ticket["backend_binding"], "sha256": sha(proof_raw)})
                    next_state["config_sha256"] = sha(canonical_bytes(ticket["backend"]))
                    if sha(canonical_bytes(next_state)) != ticket["session_sha256"]:
                        raise StateError("intake recovery backend differs from its sealed next state")
                    session.bind_recovered_backend(ticket["backend"],
                        expected_state_sha256=proof["previous_session_sha256"], binding_ref=ticket["backend_binding"])
                if sha(session.path.read_bytes()) != ticket["session_sha256"]:
                    raise StateError("intake recovery session differs from its sealed ticket")
                return tickets[0]
    def seal(path, raw):
        if path.exists() and path.read_bytes() != raw:
            raise StateError("intake recovery evidence changed: " + path.name)
        if not path.exists():
            temporary = path.with_suffix(".tmp")
            temporary.write_bytes(raw)
            temporary.replace(path)
        path.chmod(0o444)
    with control_lock(project / "output"):
        if read_control(project / "output").get("stop_requested") is not True:
            raise StateError("intake recovery requires stopped execution")
        attempt_path = (Path(attempt_path).resolve() if attempt_path is not None
                        else project / "intake-controller/attempt.json")
        if not attempt_path.is_relative_to(project / "intake-controller"):
            raise StateError("intake recovery attempt belongs to another project")
        attempt = json.loads(attempt_path.read_bytes())
        state = json.loads(session.path.read_bytes())
        current = state.get("active_job") or (state["calls"][-1]["receipt"] if state["calls"] else None)
        if current != attempt.get("receipt") or sha(Path(current).read_bytes()) != receipt_sha256:
            raise StateError("intake recovery requires its latest exactly owned attempt")
        binding = json.loads(Path(attempt["backend_binding"]).read_bytes())
        backend = validate_batch_config(binding["backend"])
        verify_batch_runtime(backend)
        composition = reasoning_off_composition(json.loads(Path(backend["composition"]).read_bytes()),
                                                backend["provider"], backend["model"])
        control = ControlStore(project)
        try:
            store = ArtifactStore(control)
            store.init_project()
            reconciliation = session.reconcile_stopped_attempt(backend, store,
                attempt_path=attempt_path, receipt_sha256=receipt_sha256)
        finally:
            control.close()
        previous_raw = session.path.read_bytes()
        previous = json.loads(previous_raw)
        directory.mkdir(parents=True, exist_ok=True)
        snapshot = directory / "previous-session.json"
        seal(snapshot, previous_raw)
        composition_path = directory / "composition.json"
        seal(composition_path, canonical_bytes(composition))
        active = deepcopy(backend)
        active["composition"] = str(composition_path)
        active["read_roots"].append(str(directory))
        active["pinned_files"][str(composition_path)] = sha(composition_path.read_bytes())
        proof = {"schema_version": "recovered-development-backend-1",
            "development_session": session.binding, "session_id": previous["session_id"],
            "previous_session": str(snapshot), "previous_session_sha256": sha(previous_raw),
            "previous_config_sha256": previous["config_sha256"], "previous_backend": backend,
            "backend": active, "backend_sha256": sha(canonical_bytes(active)),
            "reconciliation_ref": reconciliation["artifact_ref"],
            "transport_change": "explicit_openai_reasoning_effort_none"}
        proof_raw = canonical_bytes(proof)
        proof_path = directory / (sha(proof_raw) + ".json")
        seal(proof_path, proof_raw)
        next_state = deepcopy(previous)
        next_state.setdefault("backend_revisions", []).append({"path": str(proof_path), "sha256": sha(proof_raw)})
        next_state["config_sha256"] = sha(canonical_bytes(active))
        ticket = {"schema_version": "explicit-intake-continuation-1", "session_id": previous["session_id"],
            "development_session": session.binding, "preparation_sha256": sha(checkpoint_raw),
            "session_sha256": sha(canonical_bytes(next_state)),
            "control_sha256": sha((project / "output/run-control.json").read_bytes()),
            "receipt_sha256": receipt_sha256, "reconciliation_ref": reconciliation["artifact_ref"],
            "backend": active, "backend_binding": str(proof_path), "backend_binding_sha256": sha(proof_raw),
            "feedback": {"prior_receipt_sha256": receipt_sha256,
                "prior_outcome": "unknown; no scientific output accepted",
                "reported_usage": attempt["usage"], "billing_completeness": "partial_unfinalized",
                "input_delivery": "assignment.json is now pretty-printed; read its entire contents once",
                "step": "one response and at most one declared external observation, then stop"}}
        raw = canonical_bytes(ticket)
        ticket_path = directory / (sha(raw) + ".json")
        seal(ticket_path, raw)
        session.bind_recovered_backend(active, expected_state_sha256=sha(previous_raw), binding_ref=proof_path)
        return ticket_path
