"""Minimal APK checks before review, plus aapt2 metadata verification before sending.

A ZIP signature and manifest entry establish basic structure, not an app's safety,
provenance, signing identity, or distribution rights.
"""
import re
import shutil
import subprocess
import zipfile
from pathlib import Path

_BADGING = re.compile(r"^package:\s+name='([^']+)'\s+versionCode='([0-9]+)'(?:\s|$)", re.MULTILINE)


def require_apk_container(file):
    file = Path(file)
    if file.suffix.lower() != '.apk':
        return
    try:
        with zipfile.ZipFile(file) as archive:
            files = [f for f in archive.infolist() if not f.is_dir()]
            names = [f.filename for f in files]
            if len(files) < 2 or names.count('AndroidManifest.xml') != 1:
                raise ValueError('Invalid APK: missing or duplicate AndroidManifest.xml')
            for entry in files:
                chunks = entry.filename.replace('\\', '/').split('/')
                if (entry.filename.startswith('/') or
                    any(chunk in ('', '.', '..') for chunk in chunks) or
                    entry.flag_bits & 1):
                    raise ValueError('Invalid APK: unsafe or encrypted archive entry')
            if next(f for f in files if f.filename == 'AndroidManifest.xml').file_size == 0:
                raise ValueError('Invalid APK: empty AndroidManifest.xml')
    except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError) as exc:
        raise ValueError('Invalid APK: not a readable ZIP-format APK') from exc


def require_apk_identity(file, package, version, *, runner=None, aapt2=None):
    """Require Android tools to confirm claimed package and version for APK sends."""
    file = Path(file)
    if file.suffix.lower() != '.apk':
        raise ValueError('Only APK uploads have verified package identity; provider format approval needed for other types')
    require_apk_container(file)
    executable = aapt2 or shutil.which('aapt2')
    if not executable:
        raise ValueError('aapt2 is required to verify APK identity before uploading')
    if runner is None:
        runner = subprocess.run
    try:
        result = runner([executable, 'dump', 'badging', str(file)], capture_output=True,
                        text=True, check=False, timeout=45)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError('Unable to verify APK manifest with aapt2') from exc
    if result.returncode != 0:
        raise ValueError('aapt2 failed to read APK manifest')
    lines = _BADGING.findall(result.stdout)
    if len(lines) != 1:
        raise ValueError('Unable to unambiguously identify APK package and version')
    found_package, found_version = lines[0]
    if found_package != package or int(found_version) != version:
        raise ValueError('APK package/version does not match reviewed candidate')
