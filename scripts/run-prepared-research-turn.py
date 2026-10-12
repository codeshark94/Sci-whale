#!/usr/bin/env python3
"""Inspect a dormant intake or explicitly dispatch one capacity-confirmed step."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scisaurus.runtime.fresh_intake import preflight, recovery_evidence, run_once


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--runtime-python", required=True)
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--continuation", help="sealed explicit continuation ticket; consumed at most once")
    parser.add_argument("--recovery", help="JSON file containing external source and observed_at")
    parser.add_argument("--runtime-env-file", action="append", default=[], help="owner-local credential file; never copied into task inputs")
    args = parser.parse_args()
    if args.preflight:
        root, checkpoint, _, _, _ = preflight(args.root, args.runtime_python, continuation=args.continuation)
        print(json.dumps({"mission": root.name, "session_id": checkpoint["session_id"],
            "provider_calls": 0, "capacity_confirmed": False, "controller": "fresh-intake-once"}))
    else:
        recovery = json.loads(Path(args.recovery).read_text()) if args.recovery else None
        recovery_evidence(recovery)
        from scisaurus.runtime.composer import load_runtime_environment_files
        load_runtime_environment_files(args.runtime_env_file)
        try:
            record = run_once(args.root, args.runtime_python, recovery=recovery, continuation=args.continuation)
        except Exception as error:
            print(json.dumps({"status": "stopped_on_error", "error_type": type(error).__name__,
                "attempt": str(Path(args.continuation).with_suffix(".attempt.json") if args.continuation
                    else Path(args.root).resolve() / "composer/intake-controller/attempt.json")}))
            return 2
        print(json.dumps({key: record[key] for key in ("status", "session_id", "receipt", "usage", "scientific_admission")}))


if __name__ == "__main__":
    raise SystemExit(main())
