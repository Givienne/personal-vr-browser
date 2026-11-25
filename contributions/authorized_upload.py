#!/usr/bin/env python3
"""Offline preflight. Real contributions use provider_upload.py via auto_contribute.

The previous arbitrary --destination send route was removed after VRSRC review.
"""
import argparse
from pathlib import Path
from prepare_submission import export


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest='action', required=True)
    plan = actions.add_parser('plan', help='Prepare offline review manifest')
    plan.add_argument('--queue', type=Path, required=True)
    plan.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.action == 'plan':
        count = export(args.queue, args.output)
        print(f'Offline plan complete: {count} approved file(s). Nothing uploaded.')

if __name__ == '__main__':
    main()
