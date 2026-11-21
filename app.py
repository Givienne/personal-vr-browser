#!/usr/bin/env python3
"""Vialenne VR v2: minimal browser and single-source VR catalog adapter."""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timedelta, timezone
import json
import logging
import mimetypes
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import threading
from urllib.request import Request, urlopen
import vrsrc_source
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlsplit

BASE = Path(__file__).resolve().parent
WEB = BASE / "web"
DEMO = BASE / "examples" / "sample-gamelist.txt"
SAFE_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,119}$")
SAFE_FILE = re.compile(r"^[^/\\\x00-\x1f]{1,255}$")
GOOD_EXTS = (".apk", ".obb", ".zip", ".7z", ".rar", ".tar", ".gz")
IMAGE_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
MAX_COVER_BYTES = 6 * 1024 * 1024


def safe_id(value: object) -> bool:
    return isinstance(value, str) and SAFE_ID.fullmatch(value) is not None and value not in (".", "..")


def parse_gamelist(source: str) -> tuple[list[dict], int]:
    """Independent semicolon-delimited parser compatible with VRCD column names.

    Uses Python's CSV handling to tolerate quoted semicolons. Unknown optional
    columns are ignored; invalid records are skipped, with a count reported.
    """
    reader = csv.DictReader(source.lstrip("\ufeff").splitlines(), delimiter=";", quotechar='"')
    fields = {x.strip() for x in reader.fieldnames or [] if x}
    if not {"Game Name", "Package Name"}.issubset(fields):
        raise ValueError("Missing required Game Name / Package Name columns")
    entries: dict[str, dict] = {}
    skipped = 0
    for raw in reader:
        if not raw or None in raw:
            skipped += 1
            continue
        row = {(key or "").strip(): str(val or "").strip() for key, val in raw.items()}
        name = row.get("Game Name", "")
        package = row.get("Package Name", "")
        if not name or len(name) > 250 or not safe_id(package):
            skipped += 1
            continue
        try:
            popularity = float(row.get("Downloads") or 0)
        except ValueError:
            popularity = 0.0
        entry = {
            "id": package,
            "name": name,
            "packageName": package,
            "version": row.get("Version Code", ""),
            "sizeMB": row.get("Size (MB)", ""),
            "lastUpdated": row.get("Last Updated", ""),
            "releaseName": row.get("Release Name", ""),
            "downloads": max(0, popularity),
            "description": row.get("Description", ""),
            "descriptionSourceLabel": row.get("Description Source", ""),
            "descriptionSourceUrl": row.get("Description Source URL", ""),
            "descriptionLanguage": row.get("Description Language", ""),
            "demo": False,
        }
        entries[package] = entry
    return list(entries.values()), skipped


def write_catalog(entries: list[dict], data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    target = data_dir / "catalog.json"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=data_dir, delete=False) as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)
        f.write("\n")
        temp_name = f.name
    os.chmod(temp_name, 0o640)
    os.replace(temp_name, target)


def import_covers(source: Path, target: Path, entries: list[dict]) -> int:
    target.mkdir(parents=True, exist_ok=True)
    count = 0
    for entry in entries:
        package = entry["id"]
        for ext in (".jpg", ".png", ".webp", ".jpeg"):
            candidate = source / f"{package}{ext}"
            if not candidate.is_file() or candidate.is_symlink():
                continue
            if candidate.stat().st_size > MAX_COVER_BYTES:
                break
            shutil.copyfile(candidate, target / candidate.name)
            count += 1
            break
    return count


def load_games(data_dir: Path, demo: bool = False) -> list[dict]:
    if demo:
        entries, _ = parse_gamelist(DEMO.read_text("utf-8"))
        for entry in entries:
            entry["demo"] = True
        return entries
    path = data_dir / "catalog.json"
    if not path.is_file():
        return []
    data = json.loads(path.read_text("utf-8"))
    if not isinstance(data, list):
        raise ValueError("catalog.json must contain an array")
    out: list[dict] = []
    for row in data:
        if isinstance(row, dict) and safe_id(row.get("id")) and isinstance(row.get("name"), str):
            # Do not return arbitrary, unvalidated administrator-supplied fields.
            out.append({
                "id": row["id"], "name": row["name"][:250],
                "packageName": row["id"],
                "version": str(row.get("version", ""))[:100],
                "sizeMB": str(row.get("sizeMB", ""))[:60],
                "lastUpdated": str(row.get("lastUpdated", ""))[:80],
                "releaseName": str(row.get("releaseName", ""))[:250],
                "downloads": row.get("downloads", 0) if isinstance(row.get("downloads", 0), (int, float)) else 0,
                "description": str(row.get("description", ""))[:8000],
                "descriptionSourceLabel": str(row.get("descriptionSourceLabel", ""))[:150],
                "descriptionSourceUrl": str(row.get("descriptionSourceUrl", ""))[:600],
                "descriptionLanguage": str(row.get("descriptionLanguage", ""))[:20],
                "demo": False,
            })
    return out


def get_files(data_dir: Path, package: str, demo: bool = False) -> list[dict]:
    if demo or not safe_id(package):
        return []
    folder = data_dir / "packages" / package
    if not folder.is_dir() or folder.is_symlink():
        return []
    items: list[dict] = []
    for p in folder.iterdir():
        if p.is_symlink() or not p.is_file() or not SAFE_FILE.fullmatch(p.name):
            continue
        if not p.name.lower().endswith(GOOD_EXTS):
            continue
        items.append({
            "name": p.name,
            "size": p.stat().st_size,
            "url": f"/api/download/{quote(package, safe='')}/{quote(p.name, safe='')}"
        })
    return sorted(items, key=lambda x: x["name"].lower())


def image_file(data_dir: Path, package: str) -> tuple[Path, str] | None:
    if not safe_id(package):
        return None
    root = data_dir / "covers"
    for ext, ctype in IMAGE_TYPES.items():
        p = root / f"{package}{ext}"
        if p.is_file() and not p.is_symlink() and p.stat().st_size <= MAX_COVER_BYTES:
            return p, ctype
    return None


def date_timestamp(value: str) -> float:
    value = value.strip()
    if not value:
        return 0
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%m/%d/%Y", "%d/%m/%Y", "%Y/%m/%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0


class LibraryServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, addr, handler, data_dir: Path, demo: bool):
        super().__init__(addr, handler)
        self.data_dir = data_dir
        self.demo = demo


class Handler(BaseHTTPRequestHandler):
    remote_transfers = threading.BoundedSemaphore(2)
    server: LibraryServer
    protocol_version = "HTTP/1.1"
    server_version = "VialenneVR/2.0"
    sys_version = ""

    def log_message(self, fmt: str, *args):
        logging.info("%s - %s", self.address_string(), fmt % args)

    def do_GET(self):
        self.dispatch(True)

    def do_HEAD(self):
        self.dispatch(False)

    def do_POST(self):
        self.error_json(HTTPStatus.METHOD_NOT_ALLOWED, "Read-only library")

    def common_headers(self, ctype: str, length: int, status=200, extra: dict[str, str] | None = None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store" if ctype.startswith(("application/json", "text/html", "text/javascript", "text/css")) else "public, max-age=300")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()

    def output(self, content: bytes, ctype: str, send_body=True, status=200, extra=None):
        self.common_headers(ctype, len(content), status, extra)
        if send_body:
            self.wfile.write(content)

    def json_out(self, obj: object, send_body=True, status=200):
        self.output(json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode(), "application/json; charset=utf-8", send_body, status)

    def error_json(self, status: int, message: str, send_body=True):
        self.json_out({"error": message}, send_body, status)

    def send_file(self, path: Path, ctype: str, send_body: bool, download_filename: str | None = None):
        size = path.stat().st_size
        if not download_filename:
            self.common_headers(ctype, size)
            if send_body:
                with path.open("rb") as f:
                    shutil.copyfileobj(f, self.wfile, length=1024 * 1024)
            return
        range_value = self.headers.get("Range", "")
        start, end = 0, size - 1
        status = 200
        if range_value:
            m = re.fullmatch(r"bytes=(\d*)-(\d*)", range_value)
            if not m or not (m.group(1) or m.group(2)):
                return self.common_headers("text/plain", 0, 416, {"Content-Range": f"bytes */{size}"})
            a, b = m.groups()
            if not a:
                suffix = int(b)
                if suffix == 0 or size == 0:
                    return self.common_headers("text/plain", 0, 416, {"Content-Range": f"bytes */{size}"})
                start = max(0, size - suffix)
            else:
                start = int(a)
                end = min(size - 1, int(b)) if b else size - 1
            if start >= size or end < start:
                return self.common_headers("text/plain", 0, 416, {"Content-Range": f"bytes */{size}"})
            status = 206
        count = end - start + 1 if size else 0
        disposition = f"attachment; filename*=UTF-8''{quote(download_filename, safe='')}"
        extra = {"Content-Disposition": disposition, "Accept-Ranges": "bytes", "Cache-Control": "private, no-store"}
        if status == 206:
            extra["Content-Range"] = f"bytes {start}-{end}/{size}"
        self.common_headers(ctype, count, status, extra)
        if send_body:
            with path.open("rb") as f:
                f.seek(start)
                while count:
                    chunk = f.read(min(1024 * 1024, count))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    count -= len(chunk)

    def dispatch(self, send_body: bool):
        url = urlsplit(self.path)
        path = url.path
        params = parse_qs(url.query)
        try:
            if path == "/api/status":
                games = load_games(self.server.data_dir, self.server.demo)
                self.json_out({"online": True, "games": len(games), "demo": self.server.demo, "service": "vialenne-vr", "source": vrsrc_source.source_state(self.server.data_dir)}, send_body)
            elif path == "/api/games":
                self.list_games(params, send_body)
            elif path.startswith("/api/games/"):
                package = unquote(path[len("/api/games/"):])
                self.game_detail(package, send_body)
            elif path.startswith("/api/remote/"):
                parts = path[len("/api/remote/"):].split("/")
                if len(parts) != 2:
                    return self.error_json(404, "File not found", send_body)
                self.remote_download(unquote(parts[0]), unquote(parts[1]), send_body)
            elif path.startswith("/api/download/"):
                parts = path[len("/api/download/"):].split("/")
                if len(parts) != 2:
                    return self.error_json(404, "File not found", send_body)
                self.download(unquote(parts[0]), unquote(parts[1]), send_body)
            elif path.startswith("/covers/"):
                name = unquote(path[len("/covers/"):])
                image = image_file(self.server.data_dir, name)
                if image:
                    self.send_file(image[0], image[1], send_body)
                else:
                    self.error_json(404, "Cover not found", send_body)
            elif path in ("/", "/index.html", "/app.js", "/style.css", "/favicon.svg"):
                local = {"/": "index.html", "/index.html": "index.html", "/app.js": "app.js", "/style.css": "style.css", "/favicon.svg": "favicon.svg"}[path]
                ctype = {"index.html": "text/html; charset=utf-8", "app.js": "text/javascript; charset=utf-8", "style.css": "text/css; charset=utf-8", "favicon.svg": "image/svg+xml"}[local]
                self.send_file(WEB / local, ctype, send_body)
            else:
                self.error_json(404, "Not found", send_body)
        except (ValueError, json.JSONDecodeError) as exc:
            logging.warning("Bad catalog: %s", exc)
            self.error_json(500, "Unable to load catalog", send_body)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            logging.exception("Request failed")
            try:
                self.error_json(500, "Internal server error", send_body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def enrich(self, g: dict) -> dict:
        files = get_files(self.server.data_dir, g["id"], self.server.demo)
        cover = image_file(self.server.data_dir, g["id"])
        return {**g, "fileCount": len(files), "hasFiles": bool(files), "coverUrl": f"/covers/{quote(g['id'], safe='')}" if cover else None}

    def list_games(self, params: dict, send_body: bool):
        games = load_games(self.server.data_dir, self.server.demo)
        query = params.get("q", [""])[0].strip().casefold()[:150]
        sort = params.get("sort", ["name"])[0]
        order = params.get("order", ["asc"])[0]
        filter_name = params.get("filter", ["all"])[0]
        try:
            page = max(1, min(100_000, int(params.get("page", ["1"])[0])))
            per_page = max(1, min(96, int(params.get("perPage", ["24"])[0])))
        except ValueError:
            return self.error_json(400, "Invalid pagination", send_body)
        enriched = [self.enrich(g) for g in games]
        count_all = len(enriched)
        count_downloadable = sum(x["hasFiles"] for x in enriched)
        if query:
            enriched = [g for g in enriched if query in (g["name"] + " " + g["packageName"] + " " + g["releaseName"]).casefold()]
        if filter_name == "favorites":
            favorite_ids = {x for x in params.get("favorites", [""])[0].split(",") if safe_id(x)}
            enriched = [g for g in enriched if g["id"] in favorite_ids]
        if filter_name == "downloads":
            enriched = [g for g in enriched if g["hasFiles"]]
        if filter_name == "recent":
            cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).timestamp()
            enriched = [g for g in enriched if date_timestamp(g["lastUpdated"]) >= cutoff]
        def key_fn(g):
            return {
                "name": g["name"].casefold(),
                "updated": date_timestamp(g["lastUpdated"]),
                "size": tryfloat(g["sizeMB"]),
                "downloads": g["downloads"],
            }.get(sort, g["name"].casefold())
        enriched.sort(key=key_fn, reverse=order == "desc")
        count = len(enriched)
        subset = enriched[(page - 1) * per_page:page * per_page]
        self.json_out({"items": subset, "total": count, "all": count_all, "downloadable": count_downloadable, "page": page, "perPage": per_page, "demo": self.server.demo}, send_body)

    def game_detail(self, package: str, send_body: bool):
        if not safe_id(package):
            return self.error_json(404, "Game not found", send_body)
        game = next((g for g in load_games(self.server.data_dir, self.server.demo) if g["id"] == package), None)
        if game is None:
            return self.error_json(404, "Game not found", send_body)
        out = self.enrich(game)
        out["files"] = get_files(self.server.data_dir, package, self.server.demo)
        if not self.server.demo:
            try:
                source = vrsrc_source.read_source()
                if source and source.get("allowDownloads") and game.get("releaseName"):
                    remote = vrsrc_source.list_remote_files(source, game["releaseName"], package)
                    known = {f["name"] for f in out["files"]}
                    out["files"].extend(f for f in remote if f["name"] not in known)
            except (OSError, ValueError, json.JSONDecodeError):
                logging.warning("Could not read configured source")
        self.json_out(out, send_body)

    def download(self, package: str, filename: str, send_body: bool):
        if not safe_id(package) or not SAFE_FILE.fullmatch(filename) or filename in (".", ".."):
            return self.error_json(404, "File not found", send_body)
        if package not in {g["id"] for g in load_games(self.server.data_dir, self.server.demo)}:
            return self.error_json(404, "File not found", send_body)
        entries = get_files(self.server.data_dir, package, self.server.demo)
        if filename not in {g["name"] for g in entries}:
            return self.error_json(404, "File not found", send_body)
        file = self.server.data_dir / "packages" / package / filename
        if not file.is_file() or file.is_symlink():
            return self.error_json(404, "File not found", send_body)
        self.send_file(file, "application/octet-stream", send_body, filename)

    def remote_download(self, package: str, filename: str, send_body: bool):
        """Stream a file from the one configured source; never allow arbitrary URLs."""
        if not safe_id(package) or not SAFE_FILE.fullmatch(filename):
            return self.error_json(404, "File not found", send_body)
        game = next((g for g in load_games(self.server.data_dir, False) if g["id"] == package), None)
        if not game or not game.get("releaseName"):
            return self.error_json(404, "File not found", send_body)
        if not self.remote_transfers.acquire(blocking=False):
            return self.error_json(429, "Too many remote transfers", send_body)
        response_started = False
        try:
            source = vrsrc_source.read_source()
            if not vrsrc_source.provider_access_enabled() or not source or not source.get("allowDownloads"):
                return self.error_json(403, "Remote downloads not enabled", send_body)
            # Re-check the server listing so guessed paths cannot be relayed.
            allowed = {f["name"] for f in vrsrc_source.list_remote_files(source, game["releaseName"], package)}
            if filename not in allowed:
                return self.error_json(404, "File not found", send_body)
            url = vrsrc_source.remote_url(source, game["releaseName"], filename)
            headers = {"User-Agent": "personal-vr-browser", "X-API-Key": os.environ["VRSRC_API_KEY"].strip()}
            if self.headers.get("Range"):
                headers["Range"] = self.headers["Range"]
            with urlopen(Request(url, headers=headers), timeout=35) as upstream:
                status = upstream.status if upstream.status in (200, 206) else 502
                if status == 502:
                    return self.error_json(502, "Upstream download unavailable", send_body)
                self.send_response(status)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + quote(filename, safe=""))
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Cache-Control", "private, no-store")
                length = upstream.headers.get("Content-Length")
                if length and length.isdecimal():
                    self.send_header("Content-Length", length)
                else:
                    self.send_header("Connection", "close")
                    self.close_connection = True
                if upstream.headers.get("Content-Range") and status == 206:
                    self.send_header("Content-Range", upstream.headers["Content-Range"])
                if upstream.headers.get("Accept-Ranges"):
                    self.send_header("Accept-Ranges", upstream.headers["Accept-Ranges"])
                self.end_headers()
                response_started = True
                if send_body:
                    shutil.copyfileobj(upstream, self.wfile, length=1024 * 1024)
        except Exception as exc:
            logging.warning("Upstream transfer failed: %s", type(exc).__name__)
            if response_started:
                self.close_connection = True
            else:
                try:
                    self.error_json(502, "Upstream download unavailable", send_body)
                except (BrokenPipeError, ConnectionResetError):
                    pass
        finally:
            self.remote_transfers.release()


def tryfloat(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0


def cli() -> int:
    parser = argparse.ArgumentParser(description="Vialenne VR library browser")
    parser.add_argument("--data-dir", type=Path, default=BASE / "data", help="Library folder (catalog.json, covers, packages)")
    sub = parser.add_subparsers(dest="command")
    serve = sub.add_parser("serve", help="Run web server")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=4010)
    serve.add_argument("--demo", action="store_true", help="Use clearly labeled fictional example records")
    sub.add_parser("sync", help="Synchronize the one configured VR source now")
    args = parser.parse_args()
    args.data_dir = args.data_dir.expanduser().resolve()
    if args.command == "sync":
        success = vrsrc_source.sync_metadata(args.data_dir, parse_gamelist, write_catalog)
        print("Catalog sync completed" if success else "Catalog sync unavailable or failed")
        return 0 if success else 1
    if args.command not in (None, "serve"):
        parser.error("Unknown command")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args.data_dir.mkdir(parents=True, exist_ok=True)
    server = LibraryServer((getattr(args, "host", "127.0.0.1"), getattr(args, "port", 4010)), Handler, args.data_dir, getattr(args, "demo", False))
    if not server.demo:
        def periodic_sync():
            # Only one fixed source. No importing, URL picker, or browser credentials.
            while True:
                if vrsrc_source.SOURCE_FILE.is_file() and vrsrc_source.provider_access_enabled():
                    vrsrc_source.sync_metadata(args.data_dir, parse_gamelist, write_catalog)
                time.sleep(4 * 60 * 60 if vrsrc_source.source_state(args.data_dir).get("state") == "ready" else 60 * 60)
        import time
        threading.Thread(target=periodic_sync, daemon=True, name="single-source-sync").start()
    print(f"Vialenne VR listening on http://{server.server_address[0]}:{server.server_address[1]} (demo={server.demo}, data={args.data_dir})", flush=True)
    try:
        server.serve_forever(poll_interval=0.4)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(cli())
