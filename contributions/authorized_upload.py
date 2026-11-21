#!/usr/bin/env python3
"""Operator-only, approval-gated rclone transfer adapter.

The upload destination is specified explicitly by an authorized operator. This
program does not know VRSRC's contribution protocol or grant permission to use
any remote. A plan is local-only; sending requires a separately supplied,
private rclone config and affirmative authorization flags.
"""

import argparse
import configparser
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys

from contribution_queue import load_queue
from prepare_submission import export, preflight
from apk_validation import require_apk_identity

REMOTE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def private_config(path: Path, remote_name: str) -> Path:
    """Require a private, readable rclone config defining the selected remote."""
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError("Upload config must be a regular file, not a symlink")
    mode = path.stat().st_mode
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise ValueError("Upload config must not be accessible by group or others (use chmod 600)")
    config = configparser.ConfigParser(interpolation=None)
    try:
        with path.open(encoding="utf-8") as stream:
            config.read_file(stream)
    except (configparser.Error, UnicodeError) as exc:
        raise ValueError("Unable to parse private rclone configuration") from exc
    if not config.has_section(remote_name):
        raise ValueError("Destination remote is not defined in the supplied upload config")
    backend_type = config.get(remote_name, "type", fallback="").strip().lower()
    if not backend_type or backend_type == "local":
        raise ValueError("Expected a configured non-local rclone backend")
    return path.resolve(strict=True)


def destination_remote(destination: str) -> str:
    """Require an explicit, nontraversing remote file path (not a local path)."""
    if not isinstance(destination, str) or ":" not in destination:
        raise ValueError("Destination must be a provider-specified remote:file path")
    remote, relative = destination.split(":", 1)
    if not REMOTE.fullmatch(remote):
        raise ValueError("Invalid remote name")
    if (not relative or relative.startswith(("/", "\\")) or "\\" in relative
            or any(c in relative for c in "\r\n\x00")
            or any(segment in {"", ".", ".."} for segment in relative.split("/"))):
        raise ValueError("Destination must be a relative remote file path without traversal")
    return remote


def chosen_file(queue_file: Path, package: str, version: int) -> dict:
    """Always revalidate status, existence, type and SHA-256 before a send."""
    candidates = preflight(load_queue(queue_file))
    selected = [item for item in candidates
                if item["package"] == package and item["versionCode"] == version]
    if len(selected) != 1:
        raise ValueError("No unique, approved, verified attachment for this package/version")
    return selected[0]


def record_receipt(path: Path, item: dict, destination: str) -> None:
    """Record a successful rclone command without exposing credentials."""
    path = Path(path)
    if path.exists() or path.is_symlink():
        raise ValueError("Receipt path already exists")
    payload = {
        "format": "personal-vr-browser-transfer-receipt-v1",
        "package": item["package"],
        "versionCode": item["versionCode"],
        "sha256": item["sha256"],
        "destination": destination,
        "completedAt": datetime.now(timezone.utc).isoformat(),
        "note": "rclone returned success; provider acceptance was not independently verified",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2)
        stream.write("\n")


def send_one(*, queue: Path, package: str, version: int, upload_config: Path,
             destination: str, receipt: Path, acknowledged: bool,
             environ: dict | None = None) -> None:
    """Transfer one approved file only, after both local and provider authorization."""
    env = os.environ if environ is None else environ
    if not acknowledged:
        raise ValueError("Explicit provider-permission acknowledgement is required")
    if (env.get("VRSRC_ACCESS_APPROVED") != "1"
            or env.get("VRSRC_UPLOAD_AUTHORIZED") != "1"):
        raise ValueError("Upload disabled: provider access and upload authorization are not enabled")
    if Path(receipt).exists() or Path(receipt).is_symlink():
        raise ValueError("Receipt already exists; use a new receipt path for a distinct attempt")
    remote = destination_remote(destination)
    config = private_config(upload_config, remote)
    item = chosen_file(queue, package, version)
    require_apk_identity(item["file"], package, version)
    rclone = shutil.which("rclone")
    if not rclone:
        raise ValueError("rclone is not installed")
    command = [rclone, "--config", str(config), "copyto", item["file"], destination,
               "--transfers", "1", "--checkers", "1", "--tpslimit", "1",
               "--retries", "1", "--low-level-retries", "1", "--immutable"]
    # No shell, no command printing, and no credentials in arguments or receipts.
    result = subprocess.run(command, capture_output=True, text=True, check=False,
                            timeout=60 * 60)
    if result.returncode != 0:
        raise RuntimeError("rclone did not report success. Check remote status before retrying; "
                           "a partial transfer may exist")
    record_receipt(receipt, item, destination)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    plan = actions.add_parser("plan", help="Validate approved files and save an offline review manifest")
    plan.add_argument("--queue", required=True, type=Path)
    plan.add_argument("--output", required=True, type=Path)
    send = actions.add_parser("send", help="Transfer ONE approved file (requires provider permission)")
    send.add_argument("--queue", required=True, type=Path)
    send.add_argument("--package", required=True)
    send.add_argument("--version", required=True, type=int)
    send.add_argument("--upload-config", required=True, type=Path)
    send.add_argument("--destination", required=True,
                      help="Exact remote:file target supplied/approved by provider")
    send.add_argument("--receipt", required=True, type=Path)
    send.add_argument("--confirm-provider-permission", action="store_true")
    args = parser.parse_args()
    try:
        if args.action == "plan":
            count = export(args.queue, args.output)
            print(f"Offline plan complete: {count} approved file(s). Nothing uploaded.")
        else:
            send_one(queue=args.queue, package=args.package, version=args.version,
                     upload_config=args.upload_config, destination=args.destination,
                     receipt=args.receipt, acknowledged=args.confirm_provider_permission)
            print("One transfer completed according to rclone; receipt saved locally.")
    except (ValueError, OSError, RuntimeError, subprocess.TimeoutExpired,
            json.JSONDecodeError) as exc:
        # Don't print untrusted subprocess stderr; it can contain provider details.
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
