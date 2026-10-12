"""Check one response against its sealed assignment without scientific execution."""
import hashlib
import json
from pathlib import Path
import sys


def main():
    sys.path.insert(0, sys.argv[1])
    from scisaurus.runtime.intake_contract import delivered_assignment, strict_document, validate_response
    value = json.load(sys.stdin)
    raw = Path(value['assignment']).read_bytes()
    if hashlib.sha256(raw).hexdigest() != value['assignment_sha256']:
        raise ValueError('sealed assignment changed')
    validate_response(delivered_assignment(value['assignment']), strict_document(value['document']))
    print(json.dumps({'validated': True,
        'sha256': hashlib.sha256(value['document'].encode('utf-8')).hexdigest()}))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(type(error).__name__ + ': ' + str(error), file=sys.stderr)
        raise SystemExit(1)
