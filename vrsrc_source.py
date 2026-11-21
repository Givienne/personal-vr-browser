"""Single configured VR library source, based on CyberDeck's metadata conventions.

The config is server-side only; never sent to the browser. Downloads are opt-in
and must be allowed by the upstream provider and the content rights holder.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

SOURCE_FILE = Path(os.environ.get("VIALENNE_SOURCE_CONFIG", "/etc/vialenne-vr/ServerInfo.json"))
MAX_META_BYTES = 150 * 1024 * 1024
SAFE_NAME = re.compile(r"^[^/\\\x00-\x1f]{1,255}$")
ARCHIVE_EXT = re.compile(r"(?:\.7z(?:\.\d+)?|\.zip|\.apk|\.obb|\.rar)$", re.IGNORECASE)


def read_source():
    if not SOURCE_FILE.is_file():
        return None
    data = json.loads(SOURCE_FILE.read_text("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Source configuration must be an object")
    base = data.get("baseUri", "")
    encoded = data.get("password", "")
    if not isinstance(base, str) or not isinstance(encoded, str):
        raise ValueError("Source baseUri/password must be strings")
    uri = urlsplit(base)
    if uri.scheme != "https" or not uri.hostname or uri.username or uri.password or uri.query or uri.fragment:
        raise ValueError("Source baseUri must be an https URL without credentials or query parameters")
    # No secret is decoded or revealed until an actual sync.
    if len(encoded) > 1000:
        raise ValueError("Encoded password is too long")
    return {"baseUri": base.rstrip("/") + "/", "password": encoded,
            "allowDownloads": data.get("allowDownloads") is True}


def source_state(data_dir: Path):
    if not SOURCE_FILE.is_file():
        return {"state": "unconfigured", "name": "VRSRC"}
    status = data_dir / "source-status.json"
    try:
        state = json.loads(status.read_text("utf-8"))
        if state.get("state") in ("ready", "syncing", "error"):
            return {k: v for k, v in state.items() if k in ("state", "name", "lastSync")}
    except (OSError, ValueError):
        pass
    return {"state": "waiting", "name": "VRSRC"}


def write_state(data_dir: Path, state: str, last_sync: str | None = None):
    tmp = data_dir / "source-status.json.tmp"
    contents = {"state": state, "name": "VRSRC"}
    if last_sync:
        contents["lastSync"] = last_sync
    tmp.write_text(json.dumps(contents), "utf-8")
    os.replace(tmp, data_dir / "source-status.json")


def decoded_archive_password(config: dict) -> str:
    try:
        return base64.b64decode(config["password"], validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as e:
        raise ValueError("Invalid encoded archive password") from e


def rclone_flags(config):
    return ["--config", "/dev/null", "--http-url", config["baseUri"], "--tpslimit", "1.0", "--tpslimit-burst", "3"]


def source_hash(release_name: str) -> str:
    """VRCD public-endpoint convention: md5 of release name + LF."""
    return hashlib.md5((release_name + "\n").encode("utf-8")).hexdigest()


def remote_directory(config, release_name: str) -> str:
    return f":http:/{source_hash(release_name)}/"


def sync_metadata(data_dir: Path, parse_gamelist, write_catalog, max_seconds=240):
    """Fetch one source's metadata. Never download game archives in this task."""
    config = read_source()
    if config is None:
        return False
    data_dir.mkdir(parents=True, exist_ok=True)
    write_state(data_dir, "syncing")
    try:
        rclone = shutil.which("rclone")
        sevenzip = shutil.which("7zz") or shutil.which("7z")
        if not rclone or not sevenzip:
            raise RuntimeError("Missing rclone or 7z (install rclone and p7zip-full)")
        with tempfile.TemporaryDirectory(prefix="vrsrc-", dir=data_dir) as work:
            base = Path(work)
            archive = base / "meta.7z"
            extract = base / "meta"
            extract.mkdir()
            cmd = [rclone, "copyto", ":http:/meta.7z", str(archive), *rclone_flags(config), "--timeout", "40s", "--retries", "2", "--max-transfer", "150M", "--cutoff-mode", "hard"]
            subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=max_seconds)
            if archive.stat().st_size > MAX_META_BYTES:
                raise ValueError("Metadata archive exceeds the size limit")
            # `-p` without a password value causes 7z to ask on stdin rather than
            # putting a secret in the process argv. Some 7z builds may not support
            # piped prompts; fail closed rather than exposing the secret in ps.
            password = decoded_archive_password(config)
            cmd = [sevenzip, "x", "-y", "-p", str(archive), f"-o{extract}"]
            subprocess.run(cmd, input=password + "\n", capture_output=True, text=True, check=True, timeout=90)
            matches = [p for p in extract.rglob("*") if p.name.casefold().endswith("amelist.txt") and p.is_file() and not p.is_symlink()]
            if not matches:
                raise ValueError("Archive does not contain a GameList.txt")
            entries, skipped = parse_gamelist(matches[0].read_text("utf-8-sig"))
            if not entries:
                raise ValueError("Remote game list had no valid entries")
            # Copy covers before replacing the previous catalog; never follow symlinks.
            covers = data_dir / "covers"
            covers.mkdir(exist_ok=True)
            thumbdirs = [p for p in extract.rglob("thumbnails") if p.is_dir() and not p.is_symlink()]
            copied = 0
            for entry in entries:
                for thumbdir in thumbdirs[:4]:
                    candidate = thumbdir / (entry["id"] + ".jpg")
                    if candidate.is_file() and not candidate.is_symlink() and candidate.stat().st_size <= 6 * 1024 * 1024:
                        shutil.copyfile(candidate, covers / candidate.name)
                        copied += 1
                        break
            write_catalog(entries, data_dir)
            logging.info("VRSRC: synchronized %d entries, %d covers, %d skipped", len(entries), copied, skipped)
        write_state(data_dir, "ready", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        return True
    except Exception as exc:
        logging.error("VRSRC sync failed (%s)", type(exc).__name__)
        write_state(data_dir, "error")
        return False


def list_remote_files(config: dict, release: str, package: str):
    """List upstream archive parts on demand, never expose source credentials."""
    if not config.get("allowDownloads") or not release:
        return []
    rclone = shutil.which("rclone")
    if not rclone:
        return []
    cmd = [rclone, "lsjson", remote_directory(config, release), *rclone_flags(config), "--max-depth", "1", "--timeout", "20s"]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=35)
        rows = json.loads(out.stdout)
    except (OSError, ValueError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return []
    items = []
    for row in rows if isinstance(rows, list) else []:
        name = row.get("Name")
        size = row.get("Size", 0)
        if row.get("IsDir") or not isinstance(name, str) or not SAFE_NAME.fullmatch(name) or name in (".", "..") or not ARCHIVE_EXT.search(name):
            continue
        if not isinstance(size, int) or size < 0:
            continue
        items.append({"name": name, "size": size, "url": f"/api/remote/{quote(package, safe='')}/{quote(name, safe='')}"})
    return sorted(items, key=lambda x: x["name"].lower())


def remote_url(config: dict, release_name: str, filename: str):
    if not config.get("allowDownloads") or not isinstance(filename, str) or not SAFE_NAME.fullmatch(filename) or filename in (".", "..") or not ARCHIVE_EXT.search(filename):
        raise ValueError("Invalid remote file")
    return config["baseUri"] + source_hash(release_name) + "/" + quote(filename, safe="")
