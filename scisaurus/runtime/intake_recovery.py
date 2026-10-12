"""Explicit accounting and transport repair for a stopped primary intake."""
from copy import deepcopy
from contextlib import contextmanager, nullcontext
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


@contextmanager
def stopped_step(root, *, receipt_sha256, attempt_path, receipt_status):
    """Verify the exact stopped frontier; unsettled owners require official reconciliation."""
    from scisaurus.runtime.development_session import _verify_receipt_files
    import time
    root = Path(root).resolve()
    project = root / 'composer'
    pointer = json.loads((root / 'preparation.json').read_bytes())
    checkpoint_path = Path(pointer['checkpoint_path']).resolve()
    checkpoint_raw = checkpoint_path.read_bytes()
    checkpoint = json.loads(checkpoint_raw)
    if (not checkpoint_path.is_relative_to(root/'checkpoints') or checkpoint_path.stem!=sha(checkpoint_raw)
            or sha(checkpoint_raw)!=pointer['checkpoint_sha256']):
        raise StateError('completed step lost its immutable preparation')
    session = DevelopmentSession(checkpoint['development_session'])
    if Path(session.binding['project_id']) != project:
        raise StateError('completed step belongs to another project')
    with control_lock(project / 'output'):
        control_raw = (project / 'output/run-control.json').read_bytes()
        if json.loads(control_raw).get('stop_requested') is not True:
            raise StateError('completed step requires stopped execution')
        marker = Path(attempt_path).resolve()
        if not marker.is_relative_to(project / 'intake-controller'):
            raise StateError('completed predecessor has a foreign owner')
        if Path(json.loads(control_raw).get('attempt', '')).resolve() != marker:
            raise StateError('completed predecessor is not the current stopped frontier')
        marker_raw = marker.read_bytes()
        record = json.loads(marker_raw)
        sealed = project / 'intake-controller' / (sha(marker_raw) + '.json')
        if (sealed.read_bytes()!=marker_raw or record.get('schema_version')!='fresh-intake-attempt-1'
                or record.get('status') not in {'stopped_with_unreviewed_proposal','stopped_after_observation',
                                              'stopped_with_unreviewed_invention','stopped_on_error',
                                              'stopped_with_unadmitted_self_review','stopped_with_unreviewed_concepts'}):
            raise StateError('completed step requires its exact stopped marker')
        binding_path = Path(record['backend_binding']).resolve()
        if not binding_path.is_relative_to(project / 'intake-controller'):
            raise StateError('completed backend has a foreign owner')
        backend = validate_batch_config(json.loads(binding_path.read_bytes())['backend'])
        verify_batch_runtime(backend)
        initial_state=json.loads(session.path.read_bytes())
        receipt_backend_sha=sha(canonical_bytes(backend))
        if initial_state.get('config_sha256')!=receipt_backend_sha:
            revisions=initial_state.get('backend_revisions',[])
            if not revisions:
                raise StateError('stopped frontier lost its current backend lineage')
            binding_path=Path(revisions[-1]['path']).resolve()
            proof_raw=binding_path.read_bytes();proof=json.loads(proof_raw)
            if (not binding_path.is_relative_to(project/'intake-controller')
                    or sha(proof_raw)!=revisions[-1]['sha256']
                    or proof['session_id']!=initial_state['session_id']
                    or proof['development_session']!=session.binding):
                raise StateError('stopped frontier has a foreign backend revision')
            backend=validate_batch_config(proof['backend']);verify_batch_runtime(backend)
        if receipt_status=='result_unknown' and initial_state.get('active_job'):
            session.state=initial_state
            lease=nullcontext(session)
        else:
            lease=session.lease(backend,time.monotonic()+2)
        with lease:
            state_raw=session.path.read_bytes(); state=session.state
            frontier=state.get('active_job') or (state['calls'][-1]['receipt'] if state['calls'] else None)
            if (frontier!=record['receipt'] or sha(Path(frontier).read_bytes())!=receipt_sha256
                    or state.get('binding')!=session.binding
                    or state.get('config_sha256')!=sha(canonical_bytes(backend))
                    or record['session_id']!=state['session_id']
                    or record['preparation_sha256']!=sha(checkpoint_raw)):
                raise StateError('completed step requires its exact latest primary receipt')
            receipt_path=Path(record['receipt']).resolve(); receipt_raw=receipt_path.read_bytes()
            receipt=json.loads(receipt_raw)
            if (sha(receipt_raw)!=receipt_sha256 or receipt.get('status')!=receipt_status
                    or record.get('usage')!=receipt.get('usage')
                    or receipt.get('session_id')!=state['session_id']
                    or receipt.get('config_sha256')!=receipt_backend_sha
                    or receipt.get('development_session',{}).get('binding')!=session.binding
                    or receipt.get('job_directory')!=str(receipt_path.parent)
                    or receipt.get('process_reaped') is not True
                    or not receipt_path.is_relative_to(project/'intake-jobs')):
                raise StateError('predecessor is not an owned completed result')
            _verify_receipt_files(receipt_path,receipt)
            response_path=receipt_path.parent/'outputs/response.json'
            response=None
            if receipt_status=='completed':
                response=json.loads(response_path.read_bytes())
                if record.get('response')!=response or receipt['outputs']['response.json']!=sha(response_path.read_bytes()):
                    raise StateError('completed predecessor lost its exact response')
            yield {'root':root,'project':project,'checkpoint_raw':checkpoint_raw,'session':session,
                'state_raw':state_raw,'state':state,'control_raw':control_raw,'marker':marker,
                'record':record,'binding_path':binding_path,'backend':backend,'receipt_path':receipt_path,
                'receipt':receipt,'response_path':response_path,'response':response}


@contextmanager
def completed_step(root, *, receipt_sha256, attempt_path):
    with stopped_step(root, receipt_sha256=receipt_sha256, attempt_path=attempt_path,
                      receipt_status='completed') as context:
        yield context


def seal_stopped_step(context, request):
    """Publish one control-only ticket; a consumed marker cannot grant another dispatch."""
    c=context; state=c['state']; identity=state['calls'][-1]['sha256']; status=c['receipt']['status']
    body={'schema_version':'explicit-intake-continuation-1','session_id':state['session_id'],
        'development_session':c['session'].binding,'preparation_sha256':sha(c['checkpoint_raw']),
        'session_sha256':sha(c['state_raw']),'control_sha256':sha(c['control_raw']),
        'receipt_sha256':identity,'prior_receipt_status':status,'backend':c['backend'],
        'backend_binding':str(c['binding_path']),'backend_binding_sha256':sha(c['binding_path'].read_bytes()),
        **({'reconciliation_ref':c['reconciliation_ref']} if c.get('reconciliation_ref') else {}),
        'step_request':request,'feedback':{'prior_outcome':('completed but not scientifically admitted' if status=='completed'
            else 'unknown; no scientific output accepted'),'reported_usage':c['receipt']['usage'],
            **({'billing_completeness':'partial_unfinalized'} if status=='result_unknown' else {}),
            'step':request['kind']+' then stop'}}
    raw=canonical_bytes(body); directory=c['project']/'intake-controller/recoveries'/identity
    directory.mkdir(parents=True,exist_ok=True); path=directory/(sha(raw)+'.json')
    if path.with_suffix('.attempt.json').exists():raise StateError('completed step already consumed; no automatic retry')
    if path.exists() and path.read_bytes()!=raw:raise StateError('completed step ticket changed')
    if not path.exists():
        temporary=path.with_suffix('.tmp');temporary.write_bytes(raw);temporary.replace(path)
    path.chmod(0o444)
    return path


def candidate_chunks(candidates):
    """Split literal fields without changing any characters or candidate identities."""
    return [{key:[value[i:i+1000] for i in range(0,len(value),1000)] for key,value in c.items()} for c in candidates]


def prepare_literature_step(root, *, receipt_sha256, attempt_path):
    """Seal one literature acquisition from an exactly owned completed proposal."""
    from scisaurus.runtime.fresh_intake import validate_proposal
    with completed_step(root,receipt_sha256=receipt_sha256,attempt_path=attempt_path) as c:
        if c['record']['status']!='stopped_with_unreviewed_proposal' or set(c['response'])!={'topic_selection'}:
            raise StateError('literature step requires its exact completed proposal marker')
        proposal=c['response']['topic_selection']
        assignment=json.loads((c['receipt_path'].parent/'input/assignment.json').read_bytes())
        validate_proposal(proposal,assignment)
        if not proposal['candidates']:raise StateError('literature acquisition requires existing candidates')
        return seal_stopped_step(c,{'kind':'literature_acquisition','proposal_path':str(c['response_path']),
            'proposal_sha256':sha(c['response_path'].read_bytes()),'candidate_ids':[x['id'] for x in proposal['candidates']],
            'candidate_literal_chunks':candidate_chunks(proposal['candidates']),
            'rule':'Keep these candidates unchanged. Choose one targeted literature query per candidate '
                   'to find closest prior work, theoretical counterexamples and conventional application baselines. '
                   'Submit collect_literature only. All candidate fields are hypotheses, not established facts. '
                   'Metadata and reconstructed abstracts are not acquired full text or novelty validation.'})


def prepare_invention_step(root, *, receipt_sha256, attempt_path):
    """Hold the retained candidates and request function-first hypotheses from the same primary."""
    from scisaurus.runtime.invention import invention_contract, INVENTION_RULES, verify_observation, source_metadata
    with completed_step(root,receipt_sha256=receipt_sha256,attempt_path=attempt_path) as c:
        if c['record']['status']!='stopped_after_observation':
            raise StateError('invention requires its latest completed evidence observation')
        observation=c['record'].get('observation')
        if c['response']!={'tool_action':observation.get('action')}:
            raise StateError('invention observation differs from its primary tool request')
        proposal_path=None
        for call in reversed(c['state']['calls']):
            path=Path(call['receipt']).resolve().parent/'outputs/response.json'
            if path.is_file() and 'topic_selection' in json.loads(path.read_bytes()):
                proposal_path=path;break
        if proposal_path is None:raise StateError('invention has no owned retained proposal to hold')
        proposal=json.loads(proposal_path.read_bytes())['topic_selection']
        if not proposal['candidates']:raise StateError('invention hold requires retained candidates')
        verify_observation(observation,c['project'],candidate_ids=[x['id'] for x in proposal['candidates']])
        # The observation remains source data; no operator relevance or scientific verdict is supplied.
        sources=source_metadata(observation)
        request={'kind':'invention','held_proposal_path':str(proposal_path),
            'held_proposal_sha256':sha(proposal_path.read_bytes()),
            'held_candidate_ids':[x['id'] for x in proposal['candidates']],
            'held_candidate_literal_chunks':candidate_chunks(proposal['candidates']),
            'observation_path':observation['receipt'],'observation_sha256':observation['receipt_sha256'],
            'retained_source_metadata':sources,'output_contract':invention_contract(),'rules':INVENTION_RULES}
        return seal_stopped_step(c,request)


def prepare_invention_review(root, *, receipt_sha256, attempt_path):
    """Seal one explicitly authorized review without changing the completed predecessor."""
    from scisaurus.runtime.invention_review import review_request
    with completed_step(root, receipt_sha256=receipt_sha256, attempt_path=attempt_path) as context:
        return seal_stopped_step(context, review_request(context))


def prepare_concept_step(root, *, receipt_sha256, attempt_path):
    """Seal one capability-led concept design from the current stopped primary frontier."""
    from scisaurus.runtime.concept_invention import concept_request
    with completed_step(root, receipt_sha256=receipt_sha256, attempt_path=attempt_path) as context:
        return seal_stopped_step(context, concept_request(context))
