"""Explicit, isolated review feedback with durable dispatch accounting."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import uuid

from scisaurus.core.schema import canonical_bytes
from scisaurus.runtime.intake_guard import guard_backend
from scisaurus.runtime.primary_client import PrimaryClient


def review_usage_owner(receipt):
    return 'final-audit:' + str(receipt)


def request_review(configured, root, evidence, *, deadline, isolated_from, record, laboratory=None,
                   source_workbench=None):
    from scisaurus.runtime.program_sandbox import sandbox_read_grants, require_isolated_read_grants
    identity=hashlib.sha256(canonical_bytes(evidence)).hexdigest()
    root=Path(root)/identity/uuid.uuid4().hex
    policy=Path(tempfile.gettempdir())/'scisaurus-review-policies'/identity
    backend=deepcopy(configured['author_backend'])
    private=[Path(path).resolve() for path in isolated_from]
    def shared(path):
        target=Path(path).resolve()
        return all(not target.is_relative_to(owner) and not owner.is_relative_to(target) for owner in private)
    backend['read_roots']=[path for path in backend.get('read_roots',[]) if shared(path)]
    backend['pinned_files']={path:value for path,value in backend.get('pinned_files',{}).items() if shared(path)}
    backend=guard_backend(backend,policy,configured['runtime_python'],review_mode=True)
    roots=[*backend.get('read_roots',[]),str(Path(configured['runtime_python']).absolute().parent.parent)]
    grants, files=sandbox_read_grants(root/'work',backend['command'],read_only_paths=roots)
    require_isolated_read_grants(grants,files,isolated_from=isolated_from,admitted_root=root)
    from scisaurus.runtime.primary_work import work_assignment,native_tool_handler
    from scisaurus.runtime.software_workbench import SoftwareWorkbench
    assignment=deepcopy(evidence)
    computation=SoftwareWorkbench(root/'computation',deadline=deadline,laboratory=laboratory)
    if evidence.get('inputs'):
        from scisaurus.core.errors import ValidationError
        if source_workbench is None:
            raise ValidationError('review artifact inputs require their owning workbench')
        copied=source_workbench.copy_inputs_to(computation,evidence['inputs'])
        assignment['review_input_provenance']=copied
        assignment['inputs']=assignment['review_inputs']=[{'artifact_ref':row['artifact_ref'],'name':row['name']}
            for row in copied]
        assignment['review_input_usage']='Use research_run inputs to stage these verified artifacts under inputs/. Only explicitly supplied artifacts are available; producer history is excluded.'
    tools=work_assignment({}, {'kind':'research_work'})['scientific_software_tools']
    tools['actions']={key:value for key,value in tools['actions'].items()
        if key in {'check_environment','list_runtimes','inspect_runtime','run'}}
    assignment['scientific_software_tools']=tools
    if laboratory is not None:assignment['available_open_source_tools']=laboratory.context()
    dispatch={'kind':'requested_review','status':'started','request_identity':identity,'assignment':assignment,
        'backend':backend,'dispatch_root':str(root)}
    client=PrimaryClient(backend,root=root,runtime_python=configured['runtime_python'],isolated_from=isolated_from)
    client.deadline=deadline
    client.runner.task_contract['native_controller_tools']=True
    client.runner.native_tool_handler=native_tool_handler(assignment,
        computation,{})
    record(deepcopy(dispatch))
    try:
        result=client.complete(system='Independent review of explicitly supplied evidence. Perform isolated checks when useful; do not modify or replace the research author.',prompt=json.dumps(assignment))
    except Exception as error:
        if getattr(error,'receipt',None):
            dispatch.update(status='result_unknown',receipt=str(error.receipt),usage=error.usage,
                failure=error.failure_details() if callable(getattr(error,'failure_details',None)) else {'kind':type(error).__name__,'error':str(error)})
        else:dispatch['error']=str(error)
        record(deepcopy(dispatch))
        raise
    dispatch.update(status='completed',receipt=result.response_metadata['receipt'],usage=result.usage,feedback=result.json_object())
    record(deepcopy(dispatch))
    return {'outcome':'ok','feedback':deepcopy(dispatch['feedback']),'review_receipt':dispatch['receipt']}


def retain_review_dispatch(record, dispatch):
    """Update one actual dispatch while retaining other paid calls of the same evidence."""
    existing=record.setdefault('review_dispatches',[])
    existing[:]=[x for x in existing if x['dispatch_root']!=dispatch['dispatch_root']]
    existing.append(deepcopy(dispatch))
    usage={}
    for item in {x['receipt']:x for x in existing if x.get('receipt')}.values():
        for key,value in item['usage'].items():usage[key]=usage.get(key,0)+value
    record['review_usage']=usage


def receipt_has_provider_hold(path):
    """Include provider failures from nested reviewers in the owning primary turn."""
    from scisaurus.core.errors import StateError
    path=Path(path);receipt=json.loads(path.read_bytes())
    if receipt.get('provider_failure'):return True
    for call in receipt.get('native_tool_calls',[]):
        if call.get('status')!='responded':continue
        raw=(path.parent/'native-tool-results'/call['response_file']).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=call['response_sha256']:
            raise StateError('native provider failure evidence changed')
        failure=json.loads(raw).get('dispatch',{}).get('failure') or {}
        if failure.get('kind')=='provider_rate_limit' or failure.get('provider_failure'):return True
    return False


def review_receipt_evidence(dispatch):
    """Verify the isolated request and paid receipt without declaring its answer known."""
    import os
    from scisaurus.core.errors import StateError
    from scisaurus.runtime.development_session import _verify_receipt_files
    try:
        root=Path(dispatch['dispatch_root']).resolve();path=Path(dispatch['receipt']).resolve()
        raw=path.read_bytes();receipt=json.loads(raw)
        if (not path.is_relative_to(root) or path.name!='receipt.json'
                or receipt.get('schema_version')!='dsh-batch-receipt-1'
                or receipt.get('status') not in {'completed','result_unknown','not_dispatched'}
                or receipt.get('config_sha256')!=hashlib.sha256(canonical_bytes(dispatch['backend'])).hexdigest()
                or 'development_session' in receipt
                or receipt.get('usage')!=dispatch['usage']
                or any(type(v) is not int or v<0 for v in dispatch['usage'].values())
                or not {'model_calls','input_tokens','output_tokens'}.issubset(dispatch['usage'])):
            raise StateError('review receipt lost its isolated identity or paid accounting')
        if receipt['status']=='not_dispatched':
            if receipt.get('pid') is not None or receipt['usage']['model_calls']!=0:
                raise StateError('review has contradictory dispatch evidence')
        else:
            if receipt.get('process_reaped') is not True or Path(receipt['job_directory']).resolve()!=path.parent:
                raise StateError('review dispatch is not reaped')
            assignment=json.loads((path.parent/'input/assignment.json').read_bytes())
            assignment.pop('runtime_python',None)
            if assignment!=dispatch['assignment']:raise StateError('review input differs from its retained request')
            _verify_receipt_files(path,receipt)
        if type(receipt.get('pid')) is int:
            try:os.kill(receipt['pid'],0)
            except ProcessLookupError:pass
            else:raise StateError('review process is still alive')
    except (OSError,ValueError,KeyError,TypeError) as error:
        raise StateError('review accounting requires its complete actual receipt') from error
    return {'schema_version':'requested-review-reconciliation-1','receipt':str(path),
        'receipt_sha256':hashlib.sha256(raw).hexdigest(),'dispatch_sha256':hashlib.sha256(canonical_bytes(dispatch)).hexdigest(),
        'usage':deepcopy(dispatch['usage']),'answer_status':receipt['status']}


def reconcile_review(dispatch, *, receipt_sha256):
    """Record an explicit accounting-only recovery, preserving the unknown receipt."""
    from scisaurus.core.errors import StateError
    from scisaurus.runtime.intake_guard import sealed_file
    evidence=review_receipt_evidence(dispatch)
    if evidence['receipt_sha256']!=receipt_sha256:raise StateError('review recovery requires its exact receipt hash')
    directory=Path(dispatch['dispatch_root'])/'reconciliations';directory.mkdir(exist_ok=True)
    raw=canonical_bytes(evidence);path=directory/(hashlib.sha256(raw).hexdigest()+'.json');sealed_file(path,raw)
    return path


def review_is_accounted(dispatch):
    if dispatch.get('status')=='completed':return True
    from scisaurus.core.errors import StateError
    try:evidence=review_receipt_evidence(dispatch)
    except StateError:return False
    raw=canonical_bytes(evidence);path=Path(dispatch['dispatch_root'])/'reconciliations'/(hashlib.sha256(raw).hexdigest()+'.json')
    return path.is_file() and path.read_bytes()==raw
