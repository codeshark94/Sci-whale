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
from scisaurus.runtime.structured_response import preserved_document, OUTPUT_RULES

CONTINUATION_RULES = [
    'Read every current input including draft-response.json. Its literal_chunks projection preserves the interrupted authored draft and is not an accepted result. Preserve the existing capability and concept identities and continue their design under the unchanged active concept contract.',
    'Reconstruct the complete JSON object using structural keys and array positions. Preserve meaningful authored content inside the declared fields. Publish the complete response.json with the native write tool; never use substring edits on JSON deliverables.',
    'The earlier receipt remains unknown with paid usage retained. Accounting releases only the transport owner. This explicit step does not certify the earlier draft, change the science assignment or authorize a new author, topic, source retrieval or solver.',
    'Read an existing output before replacing it. Any local validation must assert the exact contract and exit nonzero on failure; stop on that failure without repair, retry or another model step.',
    'Explicit abstention is permitted if no defensible completion exists: retain the capability identities, return no final concepts and explain the limit. The interrupted concepts remain preserved in their original snapshot.'
]


def recovery_request(context, snapshot_path):
    c=context; source=c['marker'].with_name(c['marker'].name.removesuffix('.attempt.json')+'.json')
    source_raw=source.read_bytes()
    if (not source.is_relative_to(c['project']/'intake-controller/recoveries')
            or source.stem!=sha(source_raw)):
        raise StateError('structured continuation lost its original dispatch ticket')
    original=json.loads(source_raw)['step_request']
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
    raw=Path(snapshot_path).read_bytes(); snapshot=json.loads(raw)
    metadata,body=preserved_document(c['receipt_path'])
    if (Path(snapshot_path).stem!=sha(raw) or snapshot['provenance']!=metadata
            or base64.b64decode(snapshot['document_base64'],validate=True)!=body):
        raise StateError('structured continuation lost its exact interrupted draft')
    draft=json.loads(body)
    if set(draft)!={'concept_design'}:
        raise StateError('interrupted document is not a concept-design draft')
    request={'kind':'concept_continuation','active_request':deepcopy(active),
        'prior_receipt_sha256':sha(c['receipt_path'].read_bytes()),'source_ticket_sha256':sha(source_raw),
        'snapshot_path':str(Path(snapshot_path).resolve()),'snapshot_sha256':sha(raw),
        'draft_sha256':sha(body),'draft_status':'unaccepted_unknown',
        'draft_capability_ids':[row['id'] for row in draft['concept_design']['imagined_capabilities']],
        'draft_concept_ids':[row['id'] for row in draft['concept_design']['concepts']],
        'rules':CONTINUATION_RULES,'structured_output_rules':OUTPUT_RULES}
    request['delivered_input_sha256']={name:sha(value) for name,value in continuation_files(request).items()}
    return request


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


def prepare_structured_recovery(root, *, receipt_sha256, attempt_path):
    with stopped_step(root,receipt_sha256=receipt_sha256,attempt_path=attempt_path,
                      receipt_status='result_unknown') as c:
        metadata,body=preserved_document(c['receipt_path'])
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
        return seal_stopped_step(c,request)
