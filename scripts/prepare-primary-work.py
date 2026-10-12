#!/usr/bin/env python3
"""Seal same-primary work from a settled checkpoint without a model request."""
import argparse
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scisaurus.runtime.primary_work import prepare_work_step

parser=argparse.ArgumentParser(description=__doc__)
for name in ('root','receipt-sha256','attempt-path','runtime-python'):parser.add_argument('--'+name,required=True)
args=vars(parser.parse_args());root=args.pop('root');print(prepare_work_step(root,**args))
