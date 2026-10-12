"""Reconcile an owned preparation whose deterministic sandbox validation prevented launch."""
from contextlib import closing
from copy import deepcopy
import fcntl
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

from scisaurus.core.errors import StateError, ValidationError
from scisaurus.core.events import ControlStore
from scisaurus.core.schema import canonical_bytes
from scisaurus.core.store import ArtifactStore
from scisaurus.runtime.development_session import DevelopmentSession, _verify_receipt_files
from scisaurus.runtime.dsh_batch import validate_batch_config, verify_batch_runtime
from scisaurus.runtime.fresh_intake import sha
from scisaurus.runtime.run_control import control_lock


def read_proof(project, ref):
    with closing(sqlite3.connect((Path(project)/'state/control.sqlite').as_uri()+'?mode=ro',uri=True)) as connection:
        connection.row_factory=sqlite3.Row
        store=ArtifactStore(SimpleNamespace(dir=str(project),_conn=connection))
        manifest=store.get(ref);raw=store.read_body(manifest['body_hash']);proof=json.loads(raw)
        if (sha(raw)!=manifest['body_hash'] or manifest['author']!='command.recovery'
                or manifest['artifact_id']!='command/prelaunch-reconciliations/'+proof['prepared_sha256']
                or proof['schema_version']!='prelaunch-reconciliation-1'):
            raise StateError('prelaunch reconciliation lost its owned proof')
        return proof


def inspect_failure(root, *, prepared_receipt, prepared_sha256, wrapper_sha256, attempt_path):
    root=Path(root).resolve();project=root/'composer'
    pointer=json.loads((root/'preparation.json').read_bytes());checkpoint_path=Path(pointer['checkpoint_path']).resolve()
    checkpoint_raw=checkpoint_path.read_bytes();checkpoint=json.loads(checkpoint_raw)
    if not checkpoint_path.is_relative_to(root/'checkpoints') or sha(checkpoint_raw)!=pointer['checkpoint_sha256']:
        raise StateError('prelaunch recovery lost its immutable preparation')
    session=DevelopmentSession(checkpoint['development_session']);state_raw=session.path.read_bytes();state=json.loads(state_raw)
    if (Path(session.binding['project_id'])!=project or state['binding']!=session.binding
            or state['session_id']!=checkpoint['session_id']):
        raise StateError('prelaunch recovery has a foreign session')
    session.state=state;session.verify_history()
    control_raw=(project/'output/run-control.json').read_bytes();control=json.loads(control_raw)
    marker=Path(attempt_path).resolve();marker_raw=marker.read_bytes();record=json.loads(marker_raw)
    if (not marker.is_relative_to(project/'intake-controller') or Path(control['attempt']).resolve()!=marker
            or control.get('stop_requested') is not True or control.get('research_resume_authorization_ref') is not None
            or (project/'intake-controller'/(sha(marker_raw)+'.json')).read_bytes()!=marker_raw
            or record.get('schema_version')!='fresh-intake-attempt-1' or record.get('status')!='stopped_on_error'
            or record.get('scientific_admission')!='not_assessed' or record.get('session_id')!=state['session_id']
            or record.get('preparation_sha256')!=sha(checkpoint_raw)):
        raise StateError('prelaunch recovery requires its exact current stopped attempt')
    source_path=marker.with_name(marker.name.removesuffix('.attempt.json')+'.json')
    source_raw=source_path.read_bytes();source=json.loads(source_raw)
    binding=Path(record['backend_binding']).resolve();binding_raw=binding.read_bytes();backend=validate_batch_config(json.loads(binding_raw)['backend'])
    verify_batch_runtime(backend)
    if (source_path.stem!=sha(source_raw) or source['backend_binding']!=str(binding)
            or source['backend_binding_sha256']!=sha(binding_raw) or source['backend']!=backend
            or source['session_id']!=state['session_id'] or source['development_session']!=session.binding
            or not binding.is_relative_to(project/'intake-controller')
            or source.get('step_request',{}).get('kind')!='concept_continuation'):
        raise StateError('prelaunch recovery lost its original continuation')
    owned=Path(prepared_receipt).resolve();owned_raw=owned.read_bytes();prepared=json.loads(owned_raw)
    wrapper=Path(record['receipt']).resolve();wrapper_raw=wrapper.read_bytes();failure=json.loads(wrapper_raw)
    zero={'model_calls':0,'input_tokens':0,'output_tokens':0}
    wrapper_fields={'schema_version','status','config_sha256','provider','model','task_sha256','usage','error'}
    prepared_fields={'schema_version','status','config_sha256','provider','model','task_sha256','usage',
        'job_directory','session_id','development_session','input_sha256','seed_sha256','outputs','task_contract'}
    manifest_path=Path(prepared['task_contract']['path']).resolve()
    if (not owned.is_relative_to(project/'intake-jobs') or not wrapper.is_relative_to(project/'intake-jobs')
            or owned.name!='receipt.json' or wrapper.name!='receipt.json'
            or owned==wrapper or sha(owned_raw)!=prepared_sha256 or sha(wrapper_raw)!=wrapper_sha256
            or prepared.get('schema_version')!='dsh-batch-receipt-1' or set(prepared)!=prepared_fields
            or failure.get('schema_version')!='dsh-batch-receipt-1' or set(failure)!=wrapper_fields
            or set(p.name for p in wrapper.parent.iterdir())!={'receipt.json'}
            or set(p.name for p in owned.parent.iterdir())!={'receipt.json','input',manifest_path.name}
            or prepared.get('status')!='prepared' or prepared.get('usage')!=zero or prepared.get('outputs')!={}
            or prepared.get('job_directory')!=str(owned.parent) or prepared.get('session_id')!=state['session_id']
            or prepared.get('development_session',{}).get('binding')!=session.binding
            or prepared['development_session'].get('prior_receipts')!=state['calls']
            or prepared.get('config_sha256')!=sha(canonical_bytes(backend))
            or prepared.get('provider')!=backend['provider'] or prepared.get('model')!=backend['model']
            or failure.get('status')!='not_dispatched' or failure.get('usage')!=zero or record.get('usage')!=zero
            or failure.get('config_sha256')!=prepared['config_sha256']
            or failure.get('task_sha256')!=prepared['task_sha256']
            or failure.get('provider')!=backend['provider'] or failure.get('model')!=backend['model']):
        raise StateError('prelaunch recovery lacks exact never-started ownership')
    _verify_receipt_files(owned,prepared)
    manifest_raw=manifest_path.read_bytes();manifest=json.loads(manifest_raw)
    if (manifest_path.parent!=owned.parent or sha(manifest_raw)!=prepared['task_contract']['sha256']
            or manifest['inputs']!={str(owned.parent/'input'/name):identity for name,identity in prepared['input_sha256'].items()}
            or manifest['workspace']!=str(session.root/'work')):
        raise StateError('prelaunch recovery lost its exact sandbox setup')
    from scisaurus.runtime.program_sandbox import sandbox_read_grants
    try:
        sandbox_read_grants(session.root/'work',backend['command'],read_only_paths=[str(manifest_path)])
    except ValidationError as error:
        reproduced=type(error).__name__+': '+str(error)
    else:raise StateError('sandbox setup failure is not reproducible before process launch')
    if failure.get('error')!=reproduced:
        raise StateError('reproduced sandbox failure differs from its original wrapper')
    from scisaurus.runtime.concept_invention import concept_files, delivered_request
    from scisaurus.runtime.structured_recovery import continuation_files, delivered_continuation
    assignment=json.loads((owned.parent/'input/assignment.json').read_bytes());request=source['step_request']
    delivered={**concept_files(request['active_request']),**continuation_files(request)}
    if (assignment.get('concept_invention')!=delivered_request(request['active_request'])
            or assignment.get('structured_continuation')!=delivered_continuation(request)
            or any((owned.parent/'input'/name).read_bytes()!=raw for name,raw in delivered.items())
            or source['receipt_sha256']!=state['calls'][-1]['sha256']):
        raise StateError('prelaunch recovery changed its preserved concept scope')
    effective_backend=backend;effective_binding=binding
    if state['config_sha256']!=sha(canonical_bytes(backend)):
        effective_binding=Path(state['backend_revisions'][-1]['path'])
        effective_backend=validate_batch_config(json.loads(effective_binding.read_bytes())['backend'])
        verify_batch_runtime(effective_backend)
        if sha(canonical_bytes(effective_backend))!=state['config_sha256']:
            raise StateError('prelaunch recovery lost its current transport lineage')
    return {'root':root,'project':project,'session':session,'state':state,'state_raw':state_raw,
        'control_raw':control_raw,'marker':marker,'record':record,'source_path':source_path,'source_raw':source_raw,
        'source':source,'backend':effective_backend,'binding_path':effective_binding,'checkpoint_raw':checkpoint_raw,
        'prepared_path':owned,'prepared_raw':owned_raw,'wrapper_path':wrapper,'wrapper_raw':wrapper_raw,
        'reproduced_error':reproduced,'request':request}


def reconcile_failure(root, **identities):
    project=Path(root).resolve()/'composer'
    pointer=json.loads((Path(root)/'preparation.json').read_bytes());checkpoint=json.loads(Path(pointer['checkpoint_path']).read_bytes())
    session=DevelopmentSession(checkpoint['development_session'])
    with control_lock(project/'output'),(session.root/'owner.lock').open('a+b') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        c=inspect_failure(root,**identities);state=c['state']
        proof={'schema_version':'prelaunch-reconciliation-1','binding':session.binding,'session_id':state['session_id'],
            'prepared_receipt':str(c['prepared_path']),'prepared_sha256':sha(c['prepared_raw']),
            'wrapper_receipt':str(c['wrapper_path']),'wrapper_sha256':sha(c['wrapper_raw']),
            'attempt':str(c['marker']),'attempt_sha256':sha(c['marker'].read_bytes()),
            'source_ticket':str(c['source_path']),'source_ticket_sha256':sha(c['source_raw']),
            'reported_usage':json.loads(c['prepared_raw'])['usage'],'calls':state['calls'],
            'reproduced_error':c['reproduced_error'],'disposition':'never_started_no_scientific_result'}
        control=ControlStore(project)
        try:
            store=ArtifactStore(control);logical='command/prelaunch-reconciliations/'+proof['prepared_sha256']
            existing=store.head(logical)
            if state['active_job'] is None:
                membership=({'ref':existing['artifact_ref'],'prepared_sha256':proof['prepared_sha256'],
                    'wrapper_sha256':proof['wrapper_sha256']} if existing else None)
                if (not existing or read_proof(project,existing['artifact_ref'])!=proof
                        or membership not in state.get('prelaunch_reconciliations',[])):
                    raise StateError('prelaunch recovery has no owned pending preparation')
                return existing['artifact_ref']
            if state['active_job']!=str(c['prepared_path']):raise StateError('prelaunch owner changed')
            if existing and store.read_body(existing['body_hash'])!=canonical_bytes(proof):raise StateError('prelaunch proof changed')
            record=existing or store.publish_artifact(logical_id=logical,artifact_type='note',author='command.recovery',
                body=canonical_bytes(proof),media_type='application/json')
            state.setdefault('prelaunch_reconciliations',[]).append({'ref':record['artifact_ref'],
                'prepared_sha256':proof['prepared_sha256'],'wrapper_sha256':proof['wrapper_sha256']})
            state['active_job']=None;session.state=state;session.save()
            return record['artifact_ref']
        finally:control.close()


def verify_continuation(root, ticket):
    project=Path(root).resolve()/'composer';proof=read_proof(project,ticket['prelaunch_reconciliation_ref'])
    c=inspect_failure(root,prepared_receipt=proof['prepared_receipt'],prepared_sha256=proof['prepared_sha256'],
        wrapper_sha256=proof['wrapper_sha256'],attempt_path=proof['attempt'])
    if (c['state']['active_job'] is not None or c['state']['calls']!=proof['calls']
            or ticket['step_request']!=c['request']
            or proof['binding']!=c['session'].binding or proof['session_id']!=c['state']['session_id']
            or proof['attempt_sha256']!=sha(c['marker'].read_bytes())
            or proof['source_ticket_sha256']!=sha(c['source_raw'])
            or proof['reproduced_error']!=c['reproduced_error']
            or {'ref':ticket['prelaunch_reconciliation_ref'],'prepared_sha256':proof['prepared_sha256'],
                'wrapper_sha256':proof['wrapper_sha256']} not in c['state'].get('prelaunch_reconciliations',[])):
        raise StateError('prelaunch continuation changed its settled prefix or original scope')


def prepare_continuation(root, *, runtime_python, **identities):
    ref=reconcile_failure(root,**identities);c=inspect_failure(root,**identities)
    from scisaurus.runtime.structured_recovery import seal_guarded_ticket
    last=c['state']['calls'][-1];path=Path(last['receipt']);c.update(receipt=json.loads(path.read_bytes()),
        receipt_path=path,prelaunch_reconciliation_ref=ref)
    return seal_guarded_ticket(c,deepcopy(c['request']),runtime_python)
