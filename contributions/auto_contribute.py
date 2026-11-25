#!/usr/bin/env python3
"""Operator-only automatic discovery of locally available, legally owned APKs.

No headset, browser upload, manual manifest, or ADB is required. When explicitly
armed with a provider-issued upload.config and approved layout, it submits via
rclone. It is OFF by default and does not search unauthorized download sources.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sys
import time

from apk_validation import inspect_apk
from contribution_queue import sha256
from provider_upload import upload_once

MAX_APKS_PER_SCAN = 1000


def catalog_codes(catalog_file: Path):
    entries = json.loads(Path(catalog_file).read_text('utf-8'))
    if not isinstance(entries, list):
        raise ValueError('Catalog must be a list')
    known = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = entry.get('packageName') or entry.get('id')
        value = str(entry.get('version', '')).strip()
        if not isinstance(name, str) or not value.isascii() or not value.isdecimal():
            continue
        known[name] = max(int(value), known.get(name, -1))
    return known


def discover(catalog_file: Path, incoming: Path, *, inspect=None):
    incoming = Path(incoming)
    if incoming.is_symlink() or not incoming.is_dir():
        raise ValueError('Incoming directory must be a real directory')
    if incoming.stat().st_mode & 0o022:
        raise ValueError('Incoming directory must not be writable by other users')
    known = catalog_codes(catalog_file)
    inspector = inspect or inspect_apk
    out = []
    seen = set()
    files = sorted(p for p in incoming.iterdir() if p.suffix.lower() == '.apk')
    if len(files) > MAX_APKS_PER_SCAN:
        raise ValueError('Too many APKs in one scan')
    for path in files:
        if path.is_symlink() or not path.is_file():
            continue
        try:
            package, version = inspector(path)
        except (ValueError, OSError):
            # Bad APK is not considered a new release; error is operator-visible.
            continue
        if version <= known.get(package, -1):
            continue
        key = (package, version)
        if key in seen:
            continue
        seen.add(key)
        out.append({'package': package, 'versionCode': version,
                    'catalogVersionCode': known.get(package), 'file': str(path.resolve()),
                    'sha256': sha256(path), 'sizeBytes': path.stat().st_size})
    return out


def _state(path: Path):
    if path.exists() and path.is_symlink():
        raise ValueError('State file must not be a symlink')
    if not path.exists():
        return {'sent': {}}
    data = json.loads(path.read_text('utf-8'))
    if not isinstance(data, dict) or not isinstance(data.get('sent'), dict):
        raise ValueError('Invalid upload state')
    return data


def _save_state(path: Path, state: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as fp:
        json.dump(state, fp, indent=2)
        fp.write('\n')
    os.replace(tmp, path)


def scan_once(catalog: Path, incoming: Path, state_file: Path,
              *, send=False, upload_config=None, layout_file=None,
              environ=None, discoverer=None, uploader=None):
    """One scan. Only successful uploads become deduplicated receipts."""
    env = os.environ if environ is None else environ
    if send and (env.get('VRSRC_UPLOAD_AUTHORIZED') != '1'
                 or env.get('VRSRC_ACCESS_APPROVED') != '1'):
        raise PermissionError('Automatic upload requires explicit provider approval flags')
    state_file = Path(state_file)
    state_file.parent.mkdir(parents=True, exist_ok=True)
    lock = state_file.with_suffix(state_file.suffix + '.lock')
    with lock.open('a+b') as fp:
        os.chmod(lock, 0o600)
        fcntl.flock(fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        state = _state(state_file)
        candidates = (discoverer or discover)(catalog, incoming)
        fresh = []
        for item in candidates:
            identifier = f'{item["package"]}:{item["versionCode"]}:{item["sha256"]}'
            if identifier in state['sent']:
                continue
            fresh.append(item)
            if send:
                if not upload_config or not layout_file:
                    raise ValueError('Provider-issued upload.config and layout are required')
                sender = uploader or upload_once
                target = sender(item=item, upload_config=upload_config,
                                layout_file=layout_file, environ=env)
                state['sent'][identifier] = {'destination': target, 'completedAt': time.time()}
                _save_state(state_file, state)
        return fresh


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', type=Path, required=True)
    parser.add_argument('--incoming', type=Path, required=True)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--send', action='store_true', help='Activate approved provider upload')
    parser.add_argument('--upload-config', type=Path)
    parser.add_argument('--layout', type=Path, help='Provider-issued upload destination layout')
    parser.add_argument('--loop', action='store_true')
    parser.add_argument('--interval', type=int, default=3600)
    args = parser.parse_args()
    if args.loop and args.interval < 60:
        parser.error('Loop interval must be at least 60 seconds')
    try:
        while True:
            candidates = scan_once(args.catalog, args.incoming, args.state, send=args.send,
                                   upload_config=args.upload_config, layout_file=args.layout)
            print(f'{len(candidates)} new local APK release(s) identified. ' +
                  ('Transfer attempted for each.' if args.send else 'Dry run: nothing uploaded.'), flush=True)
            if not args.loop:
                break
            time.sleep(args.interval)
    except (ValueError, OSError, PermissionError, RuntimeError) as exc:
        print(f'Error: {type(exc).__name__}: {exc}', file=sys.stderr)
        raise SystemExit(1)

if __name__ == '__main__':
    main()
