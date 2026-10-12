"""Persistent native research author with automatically recorded replies."""
from scisaurus.runtime.dsh_batch import DshStructuredProducerClient
from pathlib import Path
import hashlib
import json
from scisaurus.core.schema import canonical_bytes
from scisaurus.core.errors import StateError


def current_primary_backend(binding,default):
    from scisaurus.runtime.development_session import DevelopmentSession
    session=DevelopmentSession(binding);session.state=json.loads(session.path.read_bytes())
    if session.state.get('config_sha256'):
        session._verify_backend_lineage(session.state)
    revisions=session.state.get('backend_revisions',[])
    ref=revisions[-1]['path'] if revisions else (session.state.get('pre_dispatch_binding') or {}).get('path')
    return json.loads(Path(ref).read_bytes())['backend'] if ref else default


def prepare_primary_backend(backend,root,runtime_python,binding):
    from scisaurus.runtime.intake_guard import guard_backend,sealed_file
    from scisaurus.runtime.development_session import DevelopmentSession
    session=DevelopmentSession(binding);raw=session.path.read_bytes();state=json.loads(raw)
    if state.get('active_job'):raise StateError('the primary already has an active dispatch')
    old=current_primary_backend(binding,backend)
    active=guard_backend(old,Path(root)/'native-runtime',runtime_python,work_mode=True)
    digest=lambda value:hashlib.sha256(value).hexdigest()
    if active==old:return active
    directory=Path(root)/'native-runtime/bindings';directory.mkdir(parents=True,exist_ok=True)
    snapshot=directory/(digest(raw)+'.json');sealed_file(snapshot,raw)
    if state['calls']:
        proof={'schema_version':'recovered-development-backend-1','development_session':binding,
            'session_id':state['session_id'],'previous_session':str(snapshot),'previous_session_sha256':digest(raw),
            'previous_config_sha256':state['config_sha256'],'previous_backend':old,'backend':active,
            'backend_sha256':digest(canonical_bytes(active))}
        body=canonical_bytes(proof);path=directory/(digest(body)+'.json');sealed_file(path,body)
        session.bind_recovered_backend(active,expected_state_sha256=digest(raw),binding_ref=path)
    else:
        proof={'schema_version':'unused-research-backend-binding-1','development_session':binding,
            'session_id':state['session_id'],'initial_session':str(snapshot),'initial_session_sha256':digest(raw),
            'backend':active,'backend_sha256':digest(canonical_bytes(active))}
        body=canonical_bytes(proof);path=directory/(digest(body)+'.json');sealed_file(path,body)
        session.bind_unused_backend(active,expected_state_sha256=digest(raw),binding_ref=path)
    return active


class PrimaryClient(DshStructuredProducerClient):
    def __init__(self,*args,**kwargs):
        from scisaurus.runtime.intake_guard import guarded_backend,GUARD_READY
        kwargs['required_startup_marker']=GUARD_READY
        super().__init__(*args,**kwargs)
        self.runner.task_contract={**guarded_backend(self.config),'input_delivery':'inline'}

    def output_name(self,assignment):
        return 'research-transcript.md'

    def decode_response(self,assignment,files):
        response={'notes':files['research-transcript.md'].decode('utf-8')}
        if 'research-notes.md' in files:
            response['workspace_notes']=files['research-notes.md'].decode('utf-8')
        return response

    def task(self,assignment):
        from scisaurus.runtime.primary_work import work_instructions
        if self.runner.task_contract.get('mode')=='research_review':
            return 'Read the supplied evidence and give independent review feedback. Use read tools and research_run for your own isolated checks when useful; do not modify the producer, design or results. Your reply is recorded automatically with no output format or file requirement.'
        return work_instructions()
