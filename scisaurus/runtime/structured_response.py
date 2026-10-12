"""Structural JSON publication and custody of interrupted native document writes."""
import json
from pathlib import Path

from scisaurus.core.errors import StateError
from scisaurus.runtime.fresh_intake import sha

OUTPUT_RULES = [
    'Construct and update the parsed JSON object using structural keys and array positions from output_contract. Serialize and write the complete response.json document; repeated field names or values are valid at different structural paths.',
    'The writable response.json is absent at dispatch start. Read preserved authored content from the immutable draft input when supplied; do not read response.json before its first successful write. Publish the complete document with native write, which creates it. Only a document successfully published in this current attempt can be read before replacement. Do not use substring edit operations.',
    'Read every current input through native read before workspace work. Use the authoritative validate_response tool after complete-document publication; do not author a substitute checker or extend allowed fields. A failed check or tool ends the attempt; no automatic repair step or retry.'
]


def publication_evidence(receipt_path):
    """Reproduce only successful owned write/edit effects before a terminal tool error."""
    receipt_path = Path(receipt_path).resolve(); raw = receipt_path.read_bytes(); receipt = json.loads(raw)
    if (receipt.get('status') != 'result_unknown' or receipt.get('process_reaped') is not True
            or receipt.get('finish_reason') != 'aborted'
            or Path(receipt['job_directory']).resolve() != receipt_path.parent):
        raise StateError('structured continuation requires its owned reaped tool abort')
    owner = receipt['development_session']['binding']
    output = (Path(owner['root']) / 'work/response.json').resolve()
    if not receipt_path.is_relative_to(Path(owner['project_id']).resolve()/'intake-jobs'):
        raise StateError('interrupted document belongs to another project')
    events_raw = (receipt_path.parent / 'events.jsonl').read_bytes()
    turns = set(receipt['transport_turns']); calls = {}; document = None; operations = []; failure = None
    for line in events_raw.decode().splitlines():
        params = json.loads(line).get('params', {}); event = params.get('event', {}); data = event.get('data', {})
        if params.get('sessionId') != receipt['session_id'] or data.get('turn') not in turns:
            continue
        if failure is not None and event.get('type')=='step/start':
            raise StateError('interrupted document has model steps after its terminal error')
        if event.get('type') == 'tool/call':
            if failure is not None:
                raise StateError('interrupted document has operations after its terminal error')
            calls[(data['turn'], data['callId'])] = data
        elif event.get('type') == 'tool/result':
            for block in data.get('message', {}).get('content', []):
                call = calls.get((data['turn'], block.get('toolCallId')))
                if call is None:
                    continue
                if failure is not None:
                    raise StateError('interrupted document has results after its terminal error')
                args = json.loads(call['arguments'])
                target = Path(args['file_path']) if args.get('file_path') else None
                if target is not None:
                    target = target.resolve() if target.is_absolute() else (output.parent/target).resolve()
                if block.get('isError'):
                    if (target != output or call['name'] not in {'write','edit','read'}
                            or call['name']=='read' and (document is not None
                                or any(c['name']!='read' for c in calls.values()))):
                        raise StateError('structured continuation cannot repair this terminal failure')
                    failure = {'tool': call['name'], 'call_id': call['callId'], 'turn': call['turn'],
                        'step': call['step'], 'arguments': args, 'result': block}
                    continue
                if block.get('isError') is not False or target != output:
                    continue
                if call['name'] == 'write':
                    document = args['content']
                    operations.append({'tool': 'write', 'call_id': call['callId']})
                elif call['name'] == 'edit' and document is not None:
                    old, new = args['old_string'], args['new_string']
                    if not old or not document.count(old) or not args.get('replace_all') and document.count(old) != 1:
                        raise StateError('successful edit lacks an exact deterministic match')
                    document = document.replace(old, new) if args.get('replace_all') else document.replace(old, new, 1)
                    operations.append({'tool': 'edit', 'call_id': call['callId']})
    if failure is None:
        raise StateError('structured continuation lacks owned partial document evidence')
    if document is None:
        if failure['tool']!='read' or output.exists() or output.is_symlink() or (receipt_path.parent/'outputs').exists():
            raise StateError('unpublished response differs from its native read-only abort')
        return {'receipt_sha256':sha(raw),'events_sha256':sha(events_raw),'document_sha256':None,
            'publication_state':'absent','operations':[],'failure':failure},None
    body = document.encode('utf-8')
    if output.read_bytes() != body:
        raise StateError('interrupted workspace document differs from native write/edit evidence')
    return {'receipt_sha256': sha(raw), 'events_sha256': sha(events_raw), 'document_sha256': sha(body),
            'operations': operations, 'failure': failure}, body


def preserved_document(receipt_path):
    """Require an actual authored document; an absent output cannot supply one."""
    metadata,body=publication_evidence(receipt_path)
    if body is None:
        raise StateError('structured continuation lacks owned partial document evidence')
    return metadata,body
