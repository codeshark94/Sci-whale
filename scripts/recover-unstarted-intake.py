#!/usr/bin/env python3
"""Reconcile exact never-started preparation evidence and seal one explicit continuation."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scisaurus.runtime.prelaunch_recovery import prepare_continuation


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('root','prepared-receipt','prepared-sha256','wrapper-sha256','attempt-path','runtime-python'):
        parser.add_argument('--'+name,required=True)
    args=parser.parse_args();values=vars(args);root=values.pop('root')
    print(prepare_continuation(root,**values))


if __name__=='__main__':main()
