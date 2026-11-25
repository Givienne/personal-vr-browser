"""VRSRC metadata adapter: runtime credentials, pinned HTTPS, conditional sync.

Only the operator's background sync accesses the mirror. This module exposes no
public remote listing or download APIs. Neither browser clients nor friends get
access to the application's credential or the provider's mirror files.
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
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener

from vrsrc_runtime import (AuthenticationUnavailable, MIRROR_ORIGIN,
                           NoRedirects, credentials_enabled, runtime_keys)

SOURCE_FILE = Path(os.environ.get("VIALENNE_SOURCE_CONFIG", "/etc/vialenne-vr/ServerInfo.json"))
MAX_META_BYTES = 150 * 1024 * 1024
METADATA_INTERVAL = 4 * 60 * 60  # check change before downloading again
NO_VALIDATORS_DOWNLOAD_INTERVAL = 24 * 60 * 60
META_STATE = "meta-probe.json"
META_ARCHIVE = "meta.7z"
_opener = build_opener(NoRedirects())


def provider_access_enabled() -> bool:
    return credentials_enabled()


def read_source():
    if not SOURCE_FILE.is_file():
        return None
    data = json.loads(SOURCE_FILE.read_text("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Source configuration must be an object")
    base, password = data.get("baseUri"), data.get("password", "")
    if not isinstance(base, str) or not isinstance(password, str):
        raise ValueError("Source baseUri/password must be strings")
    parsed = urlsplit(base)
    # Exact HTTPS origin: never a user-controlled mirror host or arbitrary port.
    if (parsed.scheme != "https" or parsed.hostname != "go.srcdl1.xyz"
            or parsed.netloc != "go.srcdl1.xyz" or parsed.path not in ("", "/")
            or parsed.query or parsed.fragment):
        raise ValueError("The only permitted mirror origin is https://go.srcdl1.xyz")
    if len(password) > 1000:
        raise ValueError("Archive password is too long")
    return {"baseUri": MIRROR_ORIGIN + "/", "password": password}


def source_state(data_dir: Path):
    # A status request must never fail because a private maintainer config
    # cannot be inspected by the current user (e.g. unprivileged test runs).
    try:
        configured = SOURCE_FILE.is_file()
    except OSError:
        return {"state": "error" if provider_access_enabled() else "authorization_required",
                "name": "VRSRC"}
    if not configured:
        return {"state": "unconfigured", "name": "VRSRC"}
    if not provider_access_enabled():
        return {"state": "authorization_required", "name": "VRSRC"}
    try:
        obj = json.loads((data_dir / "source-status.json").read_text("utf-8"))
        if obj.get("state") in ("ready", "syncing", "error"):
            return {k: v for k, v in obj.items() if k in ("state", "name", "lastSync")}
    except (OSError, ValueError):
        pass
    return {"state": "waiting", "name": "VRSRC"}


def write_state(data_dir: Path, state: str, last_sync: str | None = None):
    obj = {"state": state, "name": "VRSRC"}
    if last_sync:
        obj["lastSync"] = last_sync
    temp = data_dir / "source-status.json.tmp"
    temp.write_text(json.dumps(obj), "utf-8")
    os.replace(temp, data_dir / "source-status.json")


def decoded_archive_password(config: dict) -> str:
    try:
        return base64.b64decode(config.get("password", ""), validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("Invalid archive password") from exc


def source_hash(release_name: str) -> str:
    return hashlib.md5((release_name + "\n").encode("utf-8")).hexdigest()


def _mirror_request(method: str, relative: str, headers: dict | None = None):
    """Only one pinned mirror origin; never follow redirects, including after 403."""
    if not provider_access_enabled():
        raise AuthenticationUnavailable("VRSRC approval and runtime credentials required")
    if relative != "/meta.7z":
        raise ValueError("Only approved metadata resource is accessible by this adapter")
    url = MIRROR_ORIGIN + relative
    keys = runtime_keys()
    for attempt in range(2):
        key = keys.get()
        request = Request(url, headers={**(headers or {}), "X-API-Key": key,
                                        "User-Agent": "personal-vr-browser/1.2.3"}, method=method)
        try:
            response = _opener.open(request, timeout=45)
            if response.geturl() != url:
                response.close()
                raise ValueError("Unexpected mirror response origin")
            return response
        except HTTPError as error:
            if error.code == 304:
                return error
            if error.code == 403 and attempt == 0:
                keys.invalidate(forbidden=True)
                continue
            raise
    raise AuthenticationUnavailable("Mirror authentication rejected after refresh")


def _probe_headers(data_dir: Path) -> dict:
    try:
        value = json.loads((data_dir / META_STATE).read_text("utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_probe(data_dir: Path, data: dict):
    dest = data_dir / META_STATE
    temp = data_dir / (META_STATE + ".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as file:
        json.dump(data, file)
    os.replace(temp, dest)


def _same_validators(headers, state) -> bool:
    etag = headers.get("ETag")
    modified = headers.get("Last-Modified")
    if etag is not None:
        return bool(state.get("etag") and state["etag"] == etag)
    if modified is not None:
        return bool(state.get("lastModified") and state["lastModified"] == modified)
    return False


def _download_stream(response, path: Path) -> None:
    size = 0
    if int(response.headers.get("Content-Length", 0) or 0) > MAX_META_BYTES:
        raise ValueError("Metadata archive exceeds size limit")
    with path.open("xb") as target:
        while chunk := response.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_META_BYTES:
                raise ValueError("Metadata archive exceeds size limit")
            target.write(chunk)
    if not size:
        raise ValueError("Metadata archive is empty")


def _refresh_needed(data_dir: Path, meta: dict, now: float):
    archive = data_dir / META_ARCHIVE
    catalog = data_dir / "catalog.json"
    if archive.is_file() and catalog.is_file() and now - float(meta.get("checkedAt", 0)) < METADATA_INTERVAL:
        return False, None, meta
    request_headers = {}
    if archive.is_file() and catalog.is_file():
        if meta.get("etag"):
            request_headers["If-None-Match"] = meta["etag"]
        elif meta.get("lastModified"):
            request_headers["If-Modified-Since"] = meta["lastModified"]
    try:
        probe = _mirror_request("HEAD", "/meta.7z", request_headers)
    except HTTPError as exc:
        if exc.code != 405:
            raise
        # Some servers disable HEAD; a conditional GET provides equivalent checks.
        probe = None
    if probe is not None:
        with probe:
            if probe.status == 304 and archive.is_file() and catalog.is_file():
                meta["checkedAt"] = now
                _write_probe(data_dir, meta)
                return False, None, meta
            if archive.is_file() and catalog.is_file() and _same_validators(probe.headers, meta):
                meta["checkedAt"] = now
                _write_probe(data_dir, meta)
                return False, None, meta
            # If provider provides no validators, avoid a 150MB GET every 4h.
            if (archive.is_file() and catalog.is_file() and not (probe.headers.get("ETag") or probe.headers.get("Last-Modified"))
                    and now - float(meta.get("downloadedAt", 0)) < NO_VALIDATORS_DOWNLOAD_INTERVAL):
                meta["checkedAt"] = now
                _write_probe(data_dir, meta)
                return False, None, meta
            observed = {"etag": probe.headers.get("ETag"),
                        "lastModified": probe.headers.get("Last-Modified")}
    else:
        observed = {}
    return True, request_headers, {**meta, **observed}


def sync_metadata(data_dir: Path, parse_gamelist, write_catalog, max_seconds=240):
    """Maintainer-side metadata sync. Unchanged archives are not downloaded again."""
    # Do not probe or read private provider config before access is approved.
    if not provider_access_enabled():
        return False
    try:
        config = read_source()
    except (OSError, ValueError) as exc:
        logging.warning("Source configuration unavailable (%s)", type(exc).__name__)
        return False
    if config is None:
        return False
    data_dir.mkdir(parents=True, exist_ok=True)
    meta = _probe_headers(data_dir)
    now = time.time()
    try:
        needed, conditional, meta = _refresh_needed(data_dir, meta, now)
        if not needed:
            return True
    except Exception as exc:
        logging.warning("Metadata change probe failed (%s)", type(exc).__name__)
        return False
    write_state(data_dir, "syncing")
    try:
        extractor = shutil.which("7zz") or shutil.which("7z")
        if not extractor:
            raise RuntimeError("7-Zip is required to extract metadata")
        with tempfile.TemporaryDirectory(prefix="vrsrc-", dir=data_dir) as work:
            base = Path(work)
            incoming = base / "meta.7z"
            extract = base / "unpacked"
            extract.mkdir()
            response = _mirror_request("GET", "/meta.7z", conditional)
            with response:
                if response.status == 304:
                    if (data_dir / META_ARCHIVE).is_file():
                        meta["checkedAt"] = now
                        _write_probe(data_dir, meta)
                        write_state(data_dir, "ready")
                        return True
                    raise ValueError("Received 304 with no cached archive")
                if response.status != 200:
                    raise ValueError("Metadata request returned an unexpected status")
                meta["etag"] = response.headers.get("ETag") or meta.get("etag")
                meta["lastModified"] = response.headers.get("Last-Modified") or meta.get("lastModified")
                _download_stream(response, incoming)
            # The archive password is supplied by the provider's private configuration.
            password = decoded_archive_password(config)
            subprocess.run([extractor, "x", "-y", "-p", str(incoming), f"-o{extract}"],
                           input=password + "\n", capture_output=True, text=True,
                           check=True, timeout=max_seconds)
            gamelists = [p for p in extract.rglob("*")
                         if p.is_file() and not p.is_symlink() and p.name.lower().endswith("amelist.txt")]
            if len(gamelists) != 1:
                raise ValueError("Expected exactly one game list")
            entries, skipped = parse_gamelist(gamelists[0].read_text("utf-8-sig"))
            if not entries:
                raise ValueError("Metadata archive contains no valid catalog entries")
            covers = data_dir / "covers"
            covers.mkdir(exist_ok=True)
            thumbdirs = [p for p in extract.rglob("thumbnails") if p.is_dir() and not p.is_symlink()]
            copied = 0
            for entry in entries:
                for source in thumbdirs[:4]:
                    file = source / (entry["id"] + ".jpg")
                    if file.is_file() and not file.is_symlink() and file.stat().st_size <= 6 * 1024 * 1024:
                        shutil.copyfile(file, covers / file.name)
                        copied += 1
                        break
            write_catalog(entries, data_dir)
            os.replace(incoming, data_dir / META_ARCHIVE)
            meta.update(checkedAt=now, downloadedAt=now)
            _write_probe(data_dir, meta)
            logging.info("VRSRC metadata updated: %s games, %s covers, %s skipped", len(entries), copied, skipped)
        write_state(data_dir, "ready", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        return True
    except Exception as exc:
        logging.error("VRSRC metadata sync failed (%s)", type(exc).__name__)
        write_state(data_dir, "error")
        return False
