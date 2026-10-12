"""Source-pinned native tools and explicit legacy publication compatibility."""
from copy import deepcopy
import io
import json
from pathlib import Path
import zipfile

from scisaurus.core.errors import StateError
from scisaurus.core.schema import canonical_bytes
from scisaurus.runtime.fresh_intake import sha

GUARD_READY = 'SCISAURUS_NATIVE_RESEARCH_READY:tools-feedback-checkpoints-1'


def sealed_file(path, raw):
    if path.exists() and path.read_bytes() != raw:
        raise StateError('native contract policy changed: ' + str(path))
    if not path.exists():
        path.write_bytes(raw)
    path.chmod(0o444)


def guard_backend(backend, directory, runtime_python, *, work_mode=False, review_mode=False):
    """Freeze source-only validator code and a dedicated composition without changing routing."""
    source_root = Path(__file__).resolve().parents[2]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted((source_root/'scisaurus').rglob('*.py')):
            if 'tests' not in path.relative_to(source_root).parts:
                archive.writestr(zipfile.ZipInfo(path.relative_to(source_root).as_posix(),
                    date_time=(1980,1,1,0,0,0)), path.read_bytes())
    sources = {'checker.zip': buffer.getvalue(),
        **{name:(source_root/'scripts/dsh'/name).read_bytes() for name in
           ('intake-contract.mjs','check-intake-response.py','stop-on-tool-error.mjs','research-tools.json')}}
    identity = sha(canonical_bytes({**{name:sha(raw) for name,raw in sources.items()},
        **({'mode':'primary-review' if review_mode else 'primary-work'} if work_mode or review_mode else {})}))
    policy = Path(directory)/identity
    policy.mkdir(parents=True,exist_ok=True)
    for name,raw in sources.items():sealed_file(policy/name,raw)
    composition=json.loads(Path(backend['composition']).read_bytes())
    composition=[item for item in composition if item.get('id') not in {'intake-contract','stop-on-tool-error'}]
    for item in composition:
        if 'toolBash' in item.get('config', {}) or 'persona' in item.get('config', {}):
            item.setdefault('config', {})['toolBash'] = False
    if work_mode or review_mode:
        from scisaurus.runtime.primary_work import work_instructions
        composition=[item for item in composition
            if not item.get('name','').endswith('/fs-observation-policy/lib/index.js')]
        for item in composition:
            if 'persona' in item.get('config',{}):
                item['config']['persona']=('Independent read-only reviewer. Reply with evidence-grounded feedback; no research authorship or producer workspace.' if review_mode else work_instructions())
    if not (work_mode or review_mode):
        composition.append({'id':'stop-on-tool-error','name':str((policy/'stop-on-tool-error.mjs').resolve())})
    config={'python':str(Path(runtime_python).absolute()),'checker':str((policy/'check-intake-response.py').resolve()),
            'bundle':str((policy/'checker.zip').resolve())}
    if work_mode or review_mode:config['mode']='research_review' if review_mode else 'research_work'
    composition.append({'id':'intake-contract','name':str((policy/'intake-contract.mjs').resolve()),'config':config})
    sealed_file(policy/'composition.json',canonical_bytes(composition))
    active=deepcopy(backend);active['composition']=str((policy/'composition.json').resolve())
    if str(policy.resolve()) not in active['read_roots']:
        active['read_roots'].append(str(policy.resolve()))
    active['pinned_files'].update({str((policy/name).resolve()):sha((policy/name).read_bytes())
        for name in (*sources,'composition.json')})
    active['pinned_files'][config['python']]=sha(Path(config['python']).read_bytes())
    return active


def guarded_backend(backend):
    composition=json.loads(Path(backend['composition']).read_bytes())
    entries=[x for x in composition if x.get('id')=='intake-contract']
    stoppers=[x for x in composition if x.get('id')=='stop-on-tool-error']
    work= len(entries)==1 and entries[0].get('config',{}).get('mode') in {'research_work','research_review'}
    if len(entries)!=1 or len(stoppers)!=(0 if work else 1):
        raise StateError('research runtime composition differs from its sealed tool mode')
    entry=entries[0];config=entry['config']
    for path in (entry['name'],config['checker'],config['bundle'],config['python']):
        if backend['pinned_files'].get(path)!=sha(Path(path).read_bytes()):
            raise StateError('native intake policy lost its source pin')
    source_root=Path(__file__).resolve().parents[2]/'scripts/dsh'
    policies = [(entry,'intake-contract.mjs')]
    policies.extend((item,'stop-on-tool-error.mjs') for item in stoppers)
    for item,name in policies:
        digest=sha(Path(item['name']).read_bytes())
        if digest!=backend['pinned_files'].get(item['name']) or digest!=sha((source_root/name).read_bytes()):
            raise StateError('native intake policy differs from its authoritative source')
    interface=Path(entry['name']).parent/'research-tools.json'
    if (backend['pinned_files'].get(str(interface))!=sha(interface.read_bytes())
            or interface.read_bytes()!=(source_root/'research-tools.json').read_bytes()):
        raise StateError('native tool interface differs from its authoritative source')
    return config


def verify_publication(receipt_path):
    """Require an owned successful authoritative check of the exact archived response."""
    from scisaurus.runtime.input_delivery import verify_full_input_reads
    receipt_path=Path(receipt_path).resolve();receipt=json.loads(receipt_path.read_bytes())
    coverage=verify_full_input_reads(receipt_path,
        first_input='unread-input.txt' if 'unread-input.txt' in receipt['input_sha256'] else None)
    digest=receipt['outputs']['response.json'];calls={};proof=None
    for line in (receipt_path.parent/'events.jsonl').read_text().splitlines():
        row=json.loads(line);p=row.get('params',{});e=p.get('event',{});d=e.get('data',{})
        if p.get('sessionId')!=receipt['session_id'] or d.get('turn') not in receipt['transport_turns']:continue
        if e.get('type')=='tool/call':
            calls[(d['turn'],d['callId'])]=d
            if d['name']=='write':proof=None
        elif e.get('type')=='tool/result':
            for block in d.get('message',{}).get('content',[]):
                call=calls.get((d['turn'],block.get('toolCallId')))
                if not call or call['name']!='validate_response' or block.get('isError') is not False:continue
                for item in block.get('content',[]):
                    if item.get('type')!='text':continue
                    value=json.loads(item['text'])
                    if value=={'validated':True,'sha256':digest}:
                        proof={'call_id':call['callId'],'turn':call['turn'],'response_sha256':digest}
    if proof is None:raise StateError('response lacks its exact successful native contract check')
    return {**proof,'input_read_coverage':coverage}
