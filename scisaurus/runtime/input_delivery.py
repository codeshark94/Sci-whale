"""Verify native tool delivery of the exact inputs owned by a completed dispatch."""
import json
from pathlib import Path
import re

from scisaurus.core.errors import StateError
from scisaurus.runtime.fresh_intake import sha


def missing_ranges(numbers):
    ranges = []
    for number in sorted(numbers):
        if ranges and number == ranges[-1][1] + 1:
            ranges[-1][1] = number
        else:
            ranges.append([number, number])
    return ranges


def input_read_coverage(receipt_path, *, first_input=None):
    """Count matching displayed lines from owned read calls in the current turn only."""
    receipt_path = Path(receipt_path).resolve()
    raw = receipt_path.read_bytes()
    receipt = json.loads(raw)
    if Path(receipt['job_directory']).resolve() != receipt_path.parent:
        raise StateError('input delivery receipt has a foreign job owner')
    turns = set(receipt['transport_turns'])
    if not turns:
        raise StateError('input delivery requires observed transport turns')
    inputs = {}
    for name, identity in receipt['input_sha256'].items():
        path = (receipt_path.parent / 'input' / name).resolve()
        if not path.is_relative_to(receipt_path.parent / 'input') or sha(path.read_bytes()) != identity:
            raise StateError('input delivery lost its exact input: ' + name)
        inputs[str(path)] = {'name': name, 'lines': path.read_text().splitlines(), 'covered': set()}
    if not inputs:
        raise StateError('input delivery has no owned input files')
    contract=receipt.get('task_contract',{})
    if contract.get('path'):
        manifest_path=Path(contract['path']).resolve()
        if not manifest_path.is_relative_to(receipt_path.parent):
            raise StateError('input delivery manifest has a foreign owner')
        manifest_raw=manifest_path.read_bytes()
        if sha(manifest_raw)!=contract['sha256']:
            raise StateError('input delivery manifest changed')
        manifest=json.loads(manifest_raw)
        if manifest.get('input_delivery')=='inline':
            if manifest.get('inputs')!={path:receipt['input_sha256'][v['name']] for path,v in inputs.items()}:
                raise StateError('inline manifest changed its frozen input ownership')
            from scisaurus.runtime.inline_inputs import inline_inputs
            boundaries=receipt.get('turns') or [{'message_id':receipt.get('message_id'),
                'input_directory':'input','input_sha256':receipt['input_sha256']}]
            if (boundaries[0]['input_directory']!='input'
                    or boundaries[0]['input_sha256']!=receipt['input_sha256']
                    or boundaries[-1]['message_id']!=receipt.get('message_id')
                    or len({b['message_id'] for b in boundaries})!=len(boundaries)):
                raise StateError('inline messages lost their exact frozen boundary')
            events_raw=(receipt_path.parent/'events.jsonl').read_bytes()
            messages={}
            for line in events_raw.decode().splitlines():
                params=json.loads(line).get('params',{});event=params.get('event',{});data=event.get('data',{})
                if params.get('sessionId')==receipt['session_id'] and event.get('type')=='user/message':
                    messages[data.get('id')]=''.join(x.get('text','') for x in data.get('content',[]) if x.get('type')=='text')
            delivered=True;files={}
            for index,boundary in enumerate(boundaries):
                directory=(receipt_path.parent/boundary['input_directory']).resolve();content={}
                if not directory.is_relative_to(receipt_path.parent/'input'):
                    raise StateError('inline input boundary has a foreign owner')
                for name,identity in boundary['input_sha256'].items():
                    path=(directory/name).resolve()
                    if not path.is_relative_to(directory) or sha(path.read_bytes())!=identity:
                        raise StateError('inline input boundary changed')
                    content[name]=path.read_bytes()
                complete=inline_inputs(content) in messages.get(boundary['message_id'],'')
                delivered &= complete
                for name,body in content.items():
                    count=len(body.decode().splitlines());key=name if index==0 else str(index)+'/'+name
                    files[key]={'total_lines':count,'displayed_lines':count if complete else 0,
                        'missing_ranges':[] if complete else missing_ranges(set(range(1,count+1)))}
            return {'receipt_sha256':sha(raw),'events_sha256':sha(events_raw),'complete':delivered,
                'delivery':'inline','files':files}
    first = str((receipt_path.parent / 'input' / first_input).resolve()) if first_input else None
    if first is not None and first not in inputs:
        raise StateError('first input is not owned by this receipt')
    ordered = True
    events_raw = (receipt_path.parent / 'events.jsonl').read_bytes()
    calls = {}
    for line in events_raw.decode().splitlines():
        row = json.loads(line)
        params = row.get('params', {})
        event = params.get('event', {})
        data = event.get('data', {})
        if params.get('sessionId') != receipt['session_id'] or data.get('turn') not in turns:
            continue
        if event.get('type') == 'tool/call' and first is not None:
            complete_first = len(inputs[first]['covered']) == len(inputs[first]['lines'])
            arguments = json.loads(data['arguments'])
            target = str(Path(arguments.get('file_path', '')).resolve())
            if not complete_first and (data.get('name') != 'read' or target != first):
                ordered = False
        if event.get('type') == 'tool/call' and data.get('name') == 'read':
            arguments = json.loads(data['arguments'])
            path = str(Path(arguments['file_path']).resolve())
            if path in inputs:
                calls[(data['turn'], data['callId'])] = path
        elif event.get('type') == 'tool/result':
            for block in data.get('message', {}).get('content', []):
                path = calls.get((data['turn'], block.get('toolCallId')))
                if path is None or block.get('isError') is not False:
                    continue
                for item in block.get('content', []):
                    text = item.get('text', '')
                    if '<path>' + path + '</path>' not in text:
                        continue
                    for number, displayed in re.findall(r'^([0-9]+): (.*)$', text, re.M):
                        number = int(number)
                        source = inputs[path]['lines']
                        if 1 <= number <= len(source) and displayed == source[number - 1]:
                            inputs[path]['covered'].add(number)
    files = {}
    for value in inputs.values():
        missing = set(range(1, len(value['lines']) + 1)) - value['covered']
        files[value['name']] = {'total_lines': len(value['lines']),
            'displayed_lines': len(value['covered']), 'missing_ranges': missing_ranges(missing)}
    return {'receipt_sha256': sha(raw), 'events_sha256': sha(events_raw),
        'complete': ordered and all(not x['missing_ranges'] for x in files.values()), 'files': files,
        **({'delivery_order': {'first_input': first_input, 'satisfied': ordered}} if first_input else {})}


def verify_full_input_reads(receipt_path, *, first_input=None):
    coverage = input_read_coverage(receipt_path, first_input=first_input)
    if not coverage['complete']:
        missing = {name: value['missing_ranges'] for name, value in coverage['files'].items()
                   if value['missing_ranges']}
        error = StateError('primary input delivery is incomplete or out of order: ' + json.dumps(missing))
        error.input_read_coverage = coverage
        raise error
    return coverage
