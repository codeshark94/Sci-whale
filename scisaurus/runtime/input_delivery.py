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


def input_read_coverage(receipt_path):
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
    events_raw = (receipt_path.parent / 'events.jsonl').read_bytes()
    calls = {}
    for line in events_raw.decode().splitlines():
        row = json.loads(line)
        params = row.get('params', {})
        event = params.get('event', {})
        data = event.get('data', {})
        if params.get('sessionId') != receipt['session_id'] or data.get('turn') not in turns:
            continue
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
        'complete': all(not x['missing_ranges'] for x in files.values()), 'files': files}


def verify_full_input_reads(receipt_path):
    coverage = input_read_coverage(receipt_path)
    if not coverage['complete']:
        missing = {name: value['missing_ranges'] for name, value in coverage['files'].items()
                   if value['missing_ranges']}
        error = StateError('primary input delivery is incomplete: ' + json.dumps(missing))
        error.input_read_coverage = coverage
        raise error
    return coverage
