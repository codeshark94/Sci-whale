"""Structural JSON publication and custody of interrupted native document writes."""
import json
from pathlib import Path

from scisaurus.core.errors import StateError
from scisaurus.runtime.fresh_intake import sha

OUTPUT_RULES = [
    'Construct and update the parsed JSON object using structural keys and array positions from output_contract. Serialize and write the complete response.json document; repeated field names or values are valid at different structural paths.',
    'Do not use substring edit operations on a structured JSON deliverable. Read an existing document before replacing it, preserve meaningful authored content in the permitted fields, and use the native write tool for complete publication.',
    'Any local contract check must assert all required conditions and exit nonzero on failure. Printing missing or extra fields while exiting zero is not a passed check. A failed check or tool ends the attempt; no automatic repair step or retry.'
]


def preserved_document(receipt_path):
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
                    if call['name'] not in {'write', 'edit'} or target != output:
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
    if failure is None or document is None:
        raise StateError('structured continuation lacks owned partial document evidence')
    body = document.encode('utf-8')
    if output.read_bytes() != body:
        raise StateError('interrupted workspace document differs from native write/edit evidence')
    return {'receipt_sha256': sha(raw), 'events_sha256': sha(events_raw), 'document_sha256': sha(body),
            'operations': operations, 'failure': failure}, body
