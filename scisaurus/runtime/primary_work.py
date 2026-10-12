"""Same-primary work packets, recorded tools and unadmitted review feedback."""
from copy import deepcopy
import json
from pathlib import Path

from scisaurus.core.errors import StateError,ValidationError
from scisaurus.runtime.fresh_intake import sha
from scisaurus.runtime.intake_recovery import stopped_step
from scisaurus.runtime.software_workbench import tool_contract,validate_readonly_action


def native_interface():
    return json.loads((Path(__file__).resolve().parents[2]/'scripts/dsh/research-tools.json').read_bytes())


def work_instructions():
    return ' '.join(native_interface()['work_instructions'])


def checkpoint_response(receipt_path, receipt):
    """Read archived notes or historical structured output without changing its bytes."""
    name = next((name for name in ('research-transcript.md', 'research-notes.md', 'response.json')
                 if name in receipt.get('outputs', {})), 'response.json')
    path = Path(receipt_path).parent / 'outputs' / name
    raw = path.read_bytes()
    if sha(raw) != receipt['outputs'][name]:
        raise StateError('last completed work changed')
    response = {'notes': raw.decode('utf-8')} if name != 'response.json' else json.loads(raw)
    if name == 'research-transcript.md' and 'research-notes.md' in receipt['outputs']:
        notes_path = Path(receipt_path).parent / 'outputs/research-notes.md'
        notes = notes_path.read_bytes()
        if sha(notes) != receipt['outputs']['research-notes.md']:
            raise StateError('last completed notes changed')
        response['workspace_notes'] = notes.decode('utf-8')
    return path, response


def work_request(context, feedback=None):
    delivered=json.loads((context['receipt_path'].parent/'input/assignment.json').read_bytes())
    inherited=delivered.get('research_work',{}).get('checkpoint',{})
    observations=[*inherited.get('observations',[]),context['record'].get('observation') or {},
                  *context['record'].get('observations',[])]
    for call in context['state']['calls']:
        path=Path(call['receipt']);raw=path.read_bytes()
        if sha(raw)!=call['sha256']:
            raise StateError('recorded primary receipt changed')
        receipt=json.loads(raw)
        if receipt['session_id']!=context['state']['session_id']:
            raise StateError('recorded tool evidence belongs to another primary')
        for native in receipt.get('native_tool_calls',[]):
            if native.get('status')!='responded':continue
            result_path=path.parent/'native-tool-results'/native['response_file'];body=result_path.read_bytes()
            if sha(body)!=native['response_sha256']:
                raise StateError('recorded native observation changed')
            reply=json.loads(body)
            if 'observation' in reply:
                observations.append(reply['observation'])
    observations=list({x.get('receipt_ref') or (index,):x for index,x in enumerate(observations) if x}.values())
    if (any(x.get('outcome')=='result_unknown' for x in observations)
            or any(x.get('status')=='requested' for x in context['receipt'].get('native_tool_calls',[]))):
        raise StateError('unknown tool execution requires reconciliation before further work')
    response=context['response'];source_path=context['response_path']
    if response is None:
        for call in reversed(context['state']['calls']):
            path=Path(call['receipt']);receipt=json.loads(path.read_bytes())
            if receipt['status']=='completed' and ({'response.json','research-notes.md','research-transcript.md'} & receipt.get('outputs',{}).keys()):
                source_path,response=checkpoint_response(path,receipt);break
    calls={};failures=[]
    for line in (context['receipt_path'].parent/'events.jsonl').read_text().splitlines():
        row=json.loads(line);params=row.get('params',{});event=params.get('event',{});data=event.get('data',{})
        if params.get('sessionId')!=context['state']['session_id']:continue
        if event.get('type')=='tool/call':calls[data['callId']]=data
        if event.get('type')=='tool/result':
            for block in data.get('message',{}).get('content',[]):
                if block.get('isError') and block.get('toolCallId') in calls:
                    failures.append({'call':deepcopy(calls[block['toolCallId']]),'result':deepcopy(block)})
    return {'kind':'research_work',**({'feedback':feedback} if feedback is not None else {}),'checkpoint':{
        'receipt_sha256':sha(context['receipt_path'].read_bytes()),
        'prior_outcome':context['receipt']['status'],
        'reported_usage':deepcopy(context['receipt']['usage']),
        'error':context['record'].get('error'),
        'failed_native_tools':failures,
        'response_sha256':sha(source_path.read_bytes()) if response is not None else None,
        'response':deepcopy(response),
        'observation':deepcopy(context['record'].get('observation')),
        'observations':deepcopy([x for x in observations if x]),
        'review_feedback':deepcopy(context['record'].get('review_feedback'))}}


def work_assignment(original,request):
    tools=tool_contract();tools['actions'].pop('acquire');tools.pop('response')
    tools['native_tools']={'run':'research_run','other_operations':'research_tool'}
    tools['rules']=['Call research_run for computation and research_tool for other operations. Read actual observations before continuing; a successful tool is not scientific admission.']
    retired={'phase','output_contract','scientific_software_tools','execution_step','transport_feedback',
        'structured_output_policy','structured_continuation','concept_invention','invention_review',
        'invention_process','literature_acquisition'}
    return {**{key:deepcopy(value) for key,value in original.items() if key not in retired},
        'research_work':deepcopy(request),'scientific_software_tools':tools,
        'native_tool_interface':deepcopy(native_interface())}


def validate_native_action(assignment,action):
    if (not isinstance(action,dict) or set(action)!={'operation','arguments'}
            or action['operation'] not in assignment['scientific_software_tools']['actions']
            or not isinstance(action['arguments'],dict)):
        raise ValidationError('research tool request differs from its declared interface')
    if action['operation']=='run':
        from scisaurus.runtime.software_workbench import validate_run_arguments
        validate_run_arguments(action['arguments'])
    else:validate_readonly_action(action)


def review_feedback(response):
    return {'publication_interface':'valid','scientific_validation':'not_evaluated'}


def native_tool_handler(assignment,workbench,record):
    """Execute the primary's native request without another author or prompt dispatch."""
    def execute(action):
        validate_native_action(assignment,action)
        observation=workbench.execute(action)
        record.setdefault('observations',[]).append(deepcopy(observation))
        if observation.get('outcome')=='ok':workbench.retain_observation(observation)
        return observation
    return execute


def prepare_work_step(root,*,receipt_sha256,attempt_path,runtime_python,feedback=None):
    from scisaurus.runtime.run_control import save_control
    from scisaurus.runtime.structured_recovery import seal_guarded_ticket
    from scisaurus.core.events import ControlStore
    from scisaurus.core.store import ArtifactStore
    record=json.loads(Path(attempt_path).read_bytes());status=json.loads(Path(record['receipt']).read_bytes())['status']
    if status not in {'completed','result_unknown'}:
        raise StateError('work requires a settled result or an explicitly owned unknown accounting request')
    with stopped_step(root,receipt_sha256=receipt_sha256,attempt_path=attempt_path,receipt_status=status) as context:
        request=work_request(context,feedback)
        if status=='result_unknown':
            control_store=ControlStore(context['project'])
            try:
                store=ArtifactStore(control_store)
                if context['state']['active_job'] is None:
                    proof=store.get(context['state']['calls'][-1]['reconciliation_ref'])
                else:
                    proof=context['session'].reconcile_stopped_attempt(context['backend'],store,
                        attempt_path=context['marker'],receipt_sha256=receipt_sha256)
            finally:control_store.close()
            context['reconciliation_ref']=proof['artifact_ref']
            context['state_raw']=context['session'].path.read_bytes()
            context['state']=json.loads(context['state_raw'])
        control=json.loads(context['control_raw']);control['controller']='primary-work'
        save_control(context['project']/'output',control)
        context['control_raw']=(context['project']/'output/run-control.json').read_bytes()
    return seal_guarded_ticket(context,request,runtime_python)
