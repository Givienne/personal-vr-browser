#!/usr/bin/env python3
"""Private, offline review queue for authorized VR release contributions.

Does not connect to VRSRC, run rclone, or upload any file.
"""
import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import re
import sys

PACKAGE = re.compile(r"^[A-Za-z0-9._-]{1,255}$")
STATES = {"pending", "approved", "rejected"}

def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))

def save_queue(path, items):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError("Queue path must not be a symlink")
    temp = path.with_name(path.name + ".tmp")
    if temp.exists() or temp.is_symlink():
        raise ValueError("Temporary queue path already exists")
    try:
        with temp.open("x", encoding="utf-8") as stream:
            json.dump(items, stream, indent=2)
            stream.write("\n")
        temp.replace(path)
        path.chmod(0o600)
    finally:
        if temp.exists():
            temp.unlink()

def load_queue(path):
    path = Path(path)
    if not path.exists():
        return []
    data = read_json(path)
    if not isinstance(data, list):
        raise ValueError("Queue must contain a JSON list")
    return data

def add_candidates(path, candidates):
    if not isinstance(candidates, list):
        raise ValueError("Candidates must be a JSON list")
    queue = load_queue(path)
    seen = {(item["package"], item["versionCode"]) for item in queue}
    added = 0
    for item in candidates:
        if not isinstance(item, dict):
            raise ValueError("Each candidate must be an object")
        package, version = item.get("package"), item.get("installed")
        if not isinstance(package, str) or not PACKAGE.fullmatch(package):
            raise ValueError("Invalid package ID")
        if type(version) is not int or version < 0:
            raise ValueError("Invalid installed versionCode")
        key = package, version
        if key in seen:
            continue
        queue.append({"package": package, "versionCode": version,
                      "catalogVersionCode": item.get("catalog"),
                      "reason": item.get("reason", "review"),
                      "status": "pending", "file": None, "sha256": None,
                      "createdAt": datetime.now(timezone.utc).isoformat()})
        seen.add(key)
        added += 1
    if added:
        save_queue(path, queue)
    return added

def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue", type=Path, default=Path("contributions/queue.json"))
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("import", help="Import --json output from version_audit.py")
    p.add_argument("candidates", type=Path)
    sub.add_parser("list", help="List local contribution candidates")
    p = sub.add_parser("review", help="Approve/reject a candidate for later authorized submission")
    p.add_argument("package")
    p.add_argument("version", type=int)
    p.add_argument("status", choices=("approved", "rejected", "pending"))
    p = sub.add_parser("attach", help="Attach a locally owned, authorized package and its SHA-256")
    p.add_argument("package")
    p.add_argument("version", type=int)
    p.add_argument("file", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "import":
            print(f"Added {add_candidates(args.queue, read_json(args.candidates))} candidate(s)")
            return
        queue = load_queue(args.queue)
        if args.command == "list":
            for item in queue:
                print(f'{item["package"]} v{item["versionCode"]}: {item["status"]}' + (" [file attached]" if item.get("file") else ""))
            print(f"{len(queue)} candidate(s), local queue only")
            return
        matches = [x for x in queue if x["package"] == args.package and x["versionCode"] == args.version]
        if len(matches) != 1:
            raise ValueError("Candidate not found or duplicated")
        entry = matches[0]
        if args.command == "review":
            if args.status == "approved" and not entry.get("file"):
                raise ValueError("Attach a verified file before approving")
            entry["status"] = args.status
        elif args.command == "attach":
            file = args.file.resolve(strict=True)
            if not file.is_file() or file.suffix.lower() not in {".apk", ".zip", ".7z", ".obb"}:
                raise ValueError("Expected an existing APK, ZIP, 7Z, or OBB file")
            entry["file"] = str(file)
            entry["sha256"] = sha256(file)
            entry["status"] = "pending"
        save_queue(args.queue, queue)
        print("Saved locally. No files uploaded.")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
