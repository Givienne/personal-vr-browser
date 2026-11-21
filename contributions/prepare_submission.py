#!/usr/bin/env python3
"""Offline preflight and review-manifest generator. Never uploads or reads provider credentials.

Only approved records with a locally attached file and matching SHA-256 are exported.
The output is not a VRSRC upload.config or an authorization to upload.
"""
import argparse
import json
from pathlib import Path
import sys
from contribution_queue import load_queue, sha256, PACKAGE

VALID_SUFFIXES = {'.apk', '.obb', '.zip', '.7z'}


def preflight(queue):
    if not isinstance(queue, list):
        raise ValueError('Queue must be a list')
    approved = []
    seen = set()
    for entry in queue:
        if not isinstance(entry, dict):
            raise ValueError('Invalid queue entry')
        if entry.get('status') != 'approved':
            continue
        pkg = entry.get('package')
        version = entry.get('versionCode')
        if not isinstance(pkg, str) or not PACKAGE.fullmatch(pkg) or type(version) is not int or version < 0:
            raise ValueError('Invalid approved package/version')
        key = (pkg, version)
        if key in seen:
            raise ValueError(f'Duplicate approved candidate: {pkg} v{version}')
        seen.add(key)
        filename = entry.get('file')
        expected = entry.get('sha256')
        if not isinstance(filename, str) or not filename or not isinstance(expected, str) or len(expected) != 64 or any(c not in '0123456789abcdef' for c in expected):
            raise ValueError(f'Missing attachment/hash: {pkg} v{version}')
        file = Path(filename)
        if file.is_symlink() or not file.is_file() or file.suffix.lower() not in VALID_SUFFIXES:
            raise ValueError(f'Attachment missing, linked, or unsupported: {pkg} v{version}')
        actual = sha256(file)
        if actual != expected:
            raise ValueError(f'Attachment changed since review: {pkg} v{version}')
        approved.append({'package': pkg, 'versionCode': version, 'file': str(file.resolve()), 'sizeBytes': file.stat().st_size, 'sha256': actual})
    return approved


def export(queue_file, output):
    approved = preflight(load_queue(queue_file))
    output = Path(output)
    if output.exists() or output.is_symlink():
        raise ValueError('Output already exists; will not overwrite')
    output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive create with owner-only permissions from the beginning.
    import os
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump({'format': 'personal-vr-browser-offline-review-v1', 'approved': approved, 'providerSubmissionEnabled': False}, f, indent=2)
            f.write('\n')
    except BaseException:
        output.unlink(missing_ok=True)
        raise
    return len(approved)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--queue', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    try:
        n = export(args.queue, args.output)
        print(f'Preflight passed: {n} approved local file(s). Offline manifest saved to {args.output}. Nothing uploaded.')
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        raise SystemExit(1)

if __name__ == '__main__':
    main()
