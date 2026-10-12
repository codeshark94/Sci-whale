#!/usr/bin/env python3
"""Account an owned stopped call and seal one same-primary structured continuation."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scisaurus.runtime.structured_recovery import prepare_structured_recovery


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',required=True)
    parser.add_argument('--receipt-sha256',required=True)
    parser.add_argument('--attempt',required=True)
    parser.add_argument('--runtime-python',help='seal the current native publication policy with the installed checker runtime')
    args=parser.parse_args()
    print(prepare_structured_recovery(args.root,receipt_sha256=args.receipt_sha256,
        attempt_path=args.attempt,runtime_python=args.runtime_python))


if __name__=='__main__':
    main()
