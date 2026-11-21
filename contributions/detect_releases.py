#!/usr/bin/env python3
"""Compare an operator-supplied release manifest with a cached catalog.

Offline only; no network calls, uploads, headset, or ADB required.
Manifest schema: [{"package":"org.example.app", "versionCode":123}, ...]
"""
import argparse
import json
import re
from pathlib import Path

PACKAGE = re.compile(r"^[A-Za-z0-9._-]{1,255}$")


def version_code(raw):
    if isinstance(raw, bool) or not isinstance(raw, (str, int)):
        raise ValueError(f"Invalid version code: {raw!r}")
    value = str(raw).strip()
    if not value.isdecimal() or not value.isascii():
        raise ValueError(f"Version code must be a nonnegative decimal integer: {raw!r}")
    return int(value)


def candidates_for(catalog, manifest):
    if not isinstance(catalog, list) or not isinstance(manifest, list):
        raise ValueError("Catalog and manifest must each contain a JSON array")
    known = {}
    for row in catalog:
        if not isinstance(row, dict):
            raise ValueError("Invalid catalog entry")
        package = row.get("packageName") or row.get("id")
        if not isinstance(package, str) or not PACKAGE.fullmatch(package):
            raise ValueError("Invalid catalog package")
        if package in known:
            raise ValueError("Duplicate catalog package: " + package)
        known[package] = version_code(row.get("version"))
    seen = set()
    candidates = []
    for row in manifest:
        if not isinstance(row, dict):
            raise ValueError("Invalid manifest entry")
        package = row.get("package")
        if not isinstance(package, str) or not PACKAGE.fullmatch(package):
            raise ValueError("Invalid manifest package")
        if package in seen:
            raise ValueError("Duplicate manifest package: " + package)
        seen.add(package)
        newer = version_code(row.get("versionCode"))
        old = known.get(package)
        if old is None or newer > old:
            candidates.append({"package": package, "installed": newer, "catalog": old,
                               "reason": "not listed" if old is None else "newer version"})
    return sorted(candidates, key=lambda x: x["package"].casefold())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = candidates_for(json.loads(args.catalog.read_text(encoding="utf-8")),
                            json.loads(args.manifest.read_text(encoding="utf-8")))
    output = args.output
    if output.exists() or output.is_symlink():
        parser.error("Output exists; specify a new path to avoid overwriting anything")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2)
        handle.write("\n")
    output.chmod(0o600)
    print(f"Found {len(result)} candidate(s); saved locally to {output}")


if __name__ == "__main__":
    main()
