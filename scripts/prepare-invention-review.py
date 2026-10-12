#!/usr/bin/env python3
"""Seal one same-primary self-review of preserved invention hypotheses."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scisaurus.runtime.intake_recovery import prepare_invention_review


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    parser.add_argument('--receipt-sha256', required=True)
    parser.add_argument('--attempt', required=True)
    args = parser.parse_args()
    print(prepare_invention_review(args.root, receipt_sha256=args.receipt_sha256, attempt_path=args.attempt))


if __name__ == '__main__':
    main()
