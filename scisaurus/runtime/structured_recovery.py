"""Explicit continuation of a captured, unaccepted structured draft after accounting."""
import base64
from copy import deepcopy
import json
from pathlib import Path

from scisaurus.core.errors import StateError, ValidationError
from scisaurus.core.events import ControlStore
from scisaurus.core.schema import canonical_bytes
from scisaurus.core.store import ArtifactStore
from scisaurus.runtime.concept_invention import concept_contract, concept_files, delivered_request, CONCEPT_RULES
from scisaurus.runtime.dsh_batch import task_json_bytes
from scisaurus.runtime.fresh_intake import sha
from scisaurus.runtime.intake_recovery import stopped_step, seal_stopped_step
from scisaurus.runtime.invention_review import chunk_literals
from scisaurus.runtime.structured_response import publication_evidence, OUTPUT_RULES


def draft_evidence(context):
    if context['receipt']['status']=='result_unknown':
        metadata,body=publication_evidence(context['receipt_path'])
        if body is not None:return metadata,body
        source_raw,original=source_request(context)
        if original['kind']!='concept_continuation':
            raise StateError('unpublished continuation has no preserved input draft')
        assignment=json.loads((context['receipt_path'].parent/'input/assignment.json').read_bytes())
        if assignment.get('structured_continuation')!=delivered_continuation(original):
            raise StateError('unpublished continuation changed its original draft request')
        snapshot_path=Path(original['snapshot_path']).resolve()
        if not snapshot_path.is_relative_to(context['project']/'intake-controller/recoveries'):
            raise StateError('unpublished continuation has a foreign draft snapshot')
        files=continuation_files(original)
        if any((context['receipt_path'].parent/'input'/name).read_bytes()!=raw for name,raw in files.items()):
            raise StateError('unpublished continuation changed its delivered input draft')
        snapshot=json.loads(snapshot_path.read_bytes());body=base64.b64decode(snapshot['document_base64'],validate=True)
        return {**metadata,'document_sha256':sha(body),'draft_origin':'preserved_input',
            'input_draft':{'source_ticket_sha256':sha(source_raw),'snapshot_sha256':original['snapshot_sha256'],
                'draft_sha256':original['draft_sha256'],'provenance':snapshot['provenance']}},body
    if context['record']['status']!='stopped_on_error' or context['receipt']['status']!='completed':
        raise StateError('structured continuation requires an owned rejected response')
    body=context['response_path'].read_bytes()
    return {'receipt_sha256':sha(context['receipt_path'].read_bytes()),
            'events_sha256':sha((context['receipt_path'].parent/'events.jsonl').read_bytes()),
            'document_sha256':sha(body),'status':'completed_unadmitted'},body

CONTINUATION_RULES = [
    'Read every current input including draft-response.json. Its literal_chunks projection preserves the interrupted authored draft and is not an accepted result. Preserve the existing capability and concept identities and continue their design under the unchanged active concept contract.',
    'Reconstruct the complete JSON object using structural keys and array positions. Preserve meaningful authored content inside the declared fields. Publish the complete response.json with the native write tool; never use substring edits on JSON deliverables.',
    'The earlier transport receipt and paid usage retain their recorded status. This explicit step does not certify the earlier draft, change the science assignment or authorize a new author, topic, source retrieval or solver.',
    'response.json is absent at dispatch start. Do not read it before the first successful write. The preserved document is draft-response.json in the current immutable input directory. Reconstruct it there and publish directly through the native exact-contract write gate, then call validate_response. Only after current publication may the output be read before replacement. Do not author a substitute checker or widen permitted fields. Stop on failure without repair, retry or another model step.',
    'Explicit abstention is permitted if no defensible completion exists: retain the capability identities, return no final concepts and explain the limit. The interrupted concepts remain preserved in their original snapshot.'
]


def source_request(context):
    c=context; source=c['marker'].with_name(c['marker'].name.removesuffix('.attempt.json')+'.json')
    source_raw=source.read_bytes()
    if (not source.is_relative_to(c['project']/'intake-controller/recoveries')
            or source.stem!=sha(source_raw)):
        raise StateError('structured continuation lost its original dispatch ticket')
    ticket=json.loads(source_raw);receipt=c['receipt'];prior=receipt['development_session']['prior_receipts']
    if (ticket['session_id']!=receipt['session_id'] or ticket['development_session']!=c['session'].binding
            or ticket['preparation_sha256']!=sha(c['checkpoint_raw'])
            or not prior or ticket['receipt_sha256']!=prior[-1]['sha256']
            or sha(canonical_bytes(ticket['backend']))!=receipt['config_sha256']):
        raise StateError('structured continuation lost its original dispatch ownership')
    return source_raw,ticket['step_request']


def recovery_request(context, snapshot_path):
    c=context;source_raw,original=source_request(c)
    if original['kind']=='concept_invention':
        active=original
    elif original['kind']=='concept_continuation':
        active=original['active_request']
    else:
        raise StateError('structured continuation requires its active concept-design scope')
    assignment=json.loads((c['receipt_path'].parent/'input/assignment.json').read_bytes())
    if (active['kind']!='concept_invention' or assignment.get('phase')!='concept_invention'
            or assignment.get('concept_invention')!=delivered_request(active)
            or assignment.get('output_contract')!=active['output_contract']
            or active['output_contract']!=concept_contract() or active['rules']!=CONCEPT_RULES):
        raise StateError('structured continuation changed its frozen concept scope')
    if any((c['receipt_path'].parent/'input'/name).read_bytes()!=raw for name,raw in concept_files(active).items()):
        raise StateError('structured continuation changed its delivered held catalog')
    raw=Path(snapshot_path).read_bytes(); snapshot=json.loads(raw)
    metadata,body=draft_evidence(c)
    if (Path(snapshot_path).stem!=sha(raw) or snapshot['provenance']!=metadata
            or base64.b64decode(snapshot['document_base64'],validate=True)!=body):
        raise StateError('structured continuation lost its exact interrupted draft')
    draft=json.loads(body)
    if set(draft)!={'concept_design'}:
        raise StateError('interrupted document is not a concept-design draft')
    request={'kind':'concept_continuation','active_request':deepcopy(active),
        'prior_receipt_sha256':sha(c['receipt_path'].read_bytes()),'source_ticket_sha256':sha(source_raw),
        'snapshot_path':str(Path(snapshot_path).resolve()),'snapshot_sha256':sha(raw),
        'draft_sha256':sha(body),'draft_status':('unaccepted_unknown' if c['receipt']['status']=='result_unknown' else 'completed_unadmitted'),
        'draft_capability_ids':[row['id'] for row in draft['concept_design']['imagined_capabilities']],
        'draft_concept_ids':[row['id'] for row in draft['concept_design']['concepts']],
        'rules':CONTINUATION_RULES,'structured_output_rules':OUTPUT_RULES}
    request['delivered_input_sha256']={name:sha(value) for name,value in continuation_files(request).items()}
    request['prior_error']=c['record'].get('error')
    from scisaurus.runtime.intake_contract import delivered_assignment, validate_response
    from scisaurus.runtime.input_delivery import input_read_coverage
    try:
        validate_response(delivered_assignment(c['receipt_path'].parent/'input/assignment.json'), draft)
        request['prior_contract_error']=None
    except ValidationError as error:
        request['prior_contract_error']=str(error)
    request['prior_input_read_coverage']=input_read_coverage(c['receipt_path'])
    return request


def prepare_guarded_continuation(root, *, receipt_sha256, attempt_path, runtime_python):
    """Preserve a rejected owned response and revise only its native transport policy."""
    from scisaurus.runtime.intake_guard import guard_backend, guarded_backend, sealed_file
    with stopped_step(root,receipt_sha256=receipt_sha256,attempt_path=attempt_path,
                      receipt_status='completed') as c:
        metadata,body=draft_evidence(c)
        directory=c['project']/'intake-controller/recoveries'/receipt_sha256
        directory.mkdir(parents=True,exist_ok=True)
        raw=canonical_bytes({'schema_version':'stopped-structured-draft-1','provenance':metadata,
            'document_base64':base64.b64encode(body).decode('ascii'),'scientific_outcome':'not_assessed'})
        path=directory/(sha(raw)+'.json');sealed_file(path,raw)
        request=recovery_request(c,path)
    return seal_guarded_ticket(c,request,runtime_python)


def seal_guarded_ticket(c, request, runtime_python):
    from scisaurus.runtime.intake_guard import guard_backend, sealed_file
    if json.loads(c['control_raw']).get('controller')=='primary-work' and request['kind']!='research_work':
        raise StateError('primary work cannot return to the retired stage-author path')
    directory=c['project']/'intake-controller/recoveries'/c['state']['calls'][-1]['sha256']
    directory.mkdir(parents=True,exist_ok=True)
    previous_raw=c['state_raw'];previous=c['state'];old=c['backend']
    active=(guard_backend(old,directory/'contracts',runtime_python,work_mode=True)
            if request['kind']=='research_work' else guard_backend(old,directory/'contracts',runtime_python))
    from scisaurus.runtime.run_control import control_lock
    with control_lock(c['project']/'output'):
        if ((c['project']/'output/run-control.json').read_bytes()!=c['control_raw']
                or c['session'].path.read_bytes()!=previous_raw):
            raise StateError('guarded continuation lost its stopped control or session')
        if active==old:return seal_stopped_step(c,request)
        snapshots=directory/'sessions';snapshots.mkdir(exist_ok=True)
        snapshot=snapshots/(sha(previous_raw)+'.json');sealed_file(snapshot,previous_raw)
        proof={'schema_version':'recovered-development-backend-1','development_session':c['session'].binding,
            'session_id':previous['session_id'],'previous_session':str(snapshot),
            'previous_session_sha256':sha(previous_raw),'previous_config_sha256':previous['config_sha256'],
            'previous_backend':old,'backend':active,'backend_sha256':sha(canonical_bytes(active)),
            'transport_change':('primary_tools_feedback_and_checkpoints' if request['kind']=='research_work'
                                else 'native_read_before_work_and_authoritative_response_contract')}
        proof_raw=canonical_bytes(proof);binding=directory/(sha(proof_raw)+'.json');sealed_file(binding,proof_raw)
        c['session'].bind_recovered_backend(active,expected_state_sha256=sha(previous_raw),binding_ref=binding)
        c['state_raw']=c['session'].path.read_bytes();c['state']=json.loads(c['state_raw'])
        c['backend']=active;c['binding_path']=binding
        return seal_stopped_step(c,request)


def continuation_files(request):
    path=Path(request['snapshot_path']); raw=path.read_bytes(); snapshot=json.loads(raw)
    if sha(raw)!=request['snapshot_sha256'] or path.stem!=sha(raw):
        raise StateError('interrupted draft snapshot changed')
    body=base64.b64decode(snapshot['document_base64'],validate=True)
    if sha(body)!=request['draft_sha256']:
        raise StateError('interrupted draft body changed')
    files={'draft-response.json':task_json_bytes(chunk_literals(json.loads(body)))}
    if ('delivered_input_sha256' in request and
            {name:sha(value) for name,value in files.items()}!=request['delivered_input_sha256']):
        raise StateError('interrupted draft delivery projection changed')
    return files


def delivered_continuation(request):
    return {key:deepcopy(value) for key,value in request.items() if key not in {'snapshot_path','active_request'}}


def validate_continuation(response, request):
    value=response['concept_design']
    if ([row['id'] for row in value['imagined_capabilities']]!=request['draft_capability_ids']
            or value['decision']!='abstain' and [row['id'] for row in value['concepts']]!=request['draft_concept_ids']):
        raise ValidationError('structured continuation changed interrupted draft identities')


def prepare_structured_recovery(root, *, receipt_sha256, attempt_path, runtime_python=None):
    with stopped_step(root,receipt_sha256=receipt_sha256,attempt_path=attempt_path,
                      receipt_status='result_unknown') as c:
        metadata,body=draft_evidence(c)
        snapshot={'schema_version':'stopped-structured-draft-1','provenance':metadata,
            'document_base64':base64.b64encode(body).decode('ascii'),'scientific_outcome':'unknown'}
        raw=canonical_bytes(snapshot)
        directory=c['project']/'intake-controller/recoveries'/receipt_sha256
        directory.mkdir(parents=True,exist_ok=True); path=directory/(sha(raw)+'.json')
        if path.exists() and path.read_bytes()!=raw:
            raise StateError('interrupted document snapshot changed')
        if not path.exists():path.write_bytes(raw)
        path.chmod(0o444)
        request=recovery_request(c,path)
        control=ControlStore(c['project'])
        try:
            store=ArtifactStore(control); store.init_project()
            if c['state'].get('active_job') is None:
                proof=store.get(c['state']['calls'][-1]['reconciliation_ref'])
            else:
                proof=c['session'].reconcile_stopped_attempt(c['backend'],store,
                    attempt_path=c['marker'],receipt_sha256=receipt_sha256)
        finally:
            control.close()
        c['state_raw']=c['session'].path.read_bytes();c['state']=json.loads(c['state_raw'])
        c['reconciliation_ref']=proof['artifact_ref']
        if runtime_python is None:return seal_stopped_step(c,request)
    return seal_guarded_ticket(c,request,runtime_python)
