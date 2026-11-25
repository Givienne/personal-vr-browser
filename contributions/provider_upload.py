"""Locked-down, operator-only transfer using an issued upload.config and layout.

No public endpoint calls this module. It does NOT know VRSRC's undocumented
upload naming rules; the provider must issue both the config and path layout.
"""
from __future__ import annotations

import configparser
import json
import os
from pathlib import Path
import re
import shutil
import stat
import string
import subprocess

NAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$')
SAFE_SEGMENT = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._+-]{0,254}$')
SUPPORTED_FIELDS = {'package', 'versionCode', 'filename'}


def private_file(path: Path):
    path = Path(path)
    if not path.is_file() or path.is_symlink():
        raise ValueError('Provider file must be a regular file, not a symlink')
    if path.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise ValueError('Provider file must be owner-only (chmod 600)')
    return path.resolve(strict=True)


def provider_destination(config_file: Path, layout_file: Path, *, package: str,
                         versionCode: int, filename: str) -> tuple[Path, str]:
    config_file = private_file(config_file)
    layout_file = private_file(layout_file)
    if config_file.name != 'upload.config':
        raise ValueError('Use the provider-issued upload.config, never an arbitrary rclone config')
    parser = configparser.ConfigParser(interpolation=None)
    with config_file.open(encoding='utf-8') as fh:
        parser.read_file(fh)
    layout = json.loads(layout_file.read_text('utf-8'))
    if not isinstance(layout, dict):
        raise ValueError('Invalid provider upload layout')
    remote = layout.get('remote')
    template = layout.get('relativePathTemplate')
    if not isinstance(remote, str) or not NAME.fullmatch(remote):
        raise ValueError('Invalid provider upload remote')
    if not parser.has_section(remote):
        raise ValueError('Provider-upload remote missing from upload.config')
    if parser.get(remote, 'type', fallback='').lower() in {'', 'local'}:
        raise ValueError('Provider upload backend must be nonlocal')
    if (not isinstance(package, str) or not SAFE_SEGMENT.fullmatch(package)
            or type(versionCode) is not int or versionCode < 0
            or not isinstance(filename, str) or not SAFE_SEGMENT.fullmatch(filename)):
        raise ValueError('Unsafe upload item name')
    if not isinstance(template, str) or not template or len(template) > 400:
        raise ValueError('Missing provider-specified relativePathTemplate')
    fields = []
    for _, field, spec, conversion in string.Formatter().parse(template):
        if field:
            fields.append(field)
            if field not in SUPPORTED_FIELDS or spec or conversion:
                raise ValueError('Unknown upload template variable')
    if not {'package', 'versionCode', 'filename'} <= set(fields):
        raise ValueError('Upload template must include package, version and filename')
    relative = template.format(package=package, versionCode=versionCode, filename=filename)
    if (relative.startswith('/') or '\\' in relative or ':' in relative or
            any(not SAFE_SEGMENT.fullmatch(segment) or segment in {'.', '..'}
                for segment in relative.split('/'))):
        raise ValueError('Unsafe provider upload destination')
    return config_file, remote + ':' + relative


def upload_once(*, item: dict, upload_config: Path, layout_file: Path,
                environ: dict | None = None, runner=None, rclone=None) -> str:
    env = os.environ if environ is None else environ
    if env.get('VRSRC_ACCESS_APPROVED') != '1' or env.get('VRSRC_UPLOAD_AUTHORIZED') != '1':
        raise PermissionError('Provider upload is not authorized')
    from apk_validation import require_apk_identity
    from contribution_queue import sha256
    file = Path(item['file'])
    if not file.is_file() or file.is_symlink() or file.suffix.lower() != '.apk':
        raise ValueError('Only real, regular APK files are supported')
    if sha256(file) != item['sha256']:
        raise ValueError('APK changed since detection')
    package, version = item['package'], item['versionCode']
    require_apk_identity(file, package, version)
    config, target = provider_destination(upload_config, layout_file,
                                          package=package, versionCode=version, filename=file.name)
    rclone = rclone or shutil.which('rclone')
    if not rclone:
        raise ValueError('rclone is required for approved uploads')
    runner = runner or subprocess.run
    # No shell, no API key from mirror added to rclone, and no dynamic remote URL.
    command = [rclone, '--config', str(config), 'copyto', str(file), target,
               '--transfers', '1', '--checkers', '1', '--tpslimit', '1',
               '--retries', '1', '--low-level-retries', '1', '--immutable']
    result = runner(command, capture_output=True, text=True, timeout=3600, check=False)
    if result.returncode:
        raise RuntimeError('Provider upload failed or was incomplete; inspect remote before retrying')
    return target
