#!/usr/bin/env python3
"""Seal paid unknown accounting and one explicit transport continuation without dispatch."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scisaurus.runtime.intake_recovery import prepare_continuation


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    parser.add_argument('--receipt-sha256', required=True)
    args = parser.parse_args()
    print(prepare_continuation(args.root, receipt_sha256=args.receipt_sha256))


if __name__ == '__main__':
    main()
