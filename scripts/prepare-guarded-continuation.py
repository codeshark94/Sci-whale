#!/usr/bin/env python3
"""Seal one same-primary continuation with native input and publication gates."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scisaurus.runtime.structured_recovery import prepare_guarded_continuation


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('root','receipt-sha256','attempt','runtime-python'):
        parser.add_argument('--'+name,required=True)
    args=parser.parse_args()
    print(prepare_guarded_continuation(args.root,receipt_sha256=args.receipt_sha256,
        attempt_path=args.attempt,runtime_python=args.runtime_python))


if __name__=='__main__':main()
