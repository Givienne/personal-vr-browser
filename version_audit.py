#!/usr/bin/env python3
"""Compare an operator-provided device inventory with the cached catalog.

This is a local review tool. It never uploads device data or app packages.
"""
import argparse
import json
from pathlib import Path
import re

PACKAGE_LINE = re.compile(r"^package:([A-Za-z0-9._-]+)\s+versionCode:(\d+)$")


def installed_versions(raw: str) -> dict[str, int]:
    versions = {}
    for line in raw.splitlines():
        match = PACKAGE_LINE.fullmatch(line.strip())
        if match:
            versions[match[1]] = int(match[2])
    return versions


def find_updates(catalog: list[dict], installed: dict[str, int]) -> list[dict]:
    known = {game["id"]: game for game in catalog if isinstance(game, dict) and isinstance(game.get("id"), str)}
    candidates = []
    for package, version in sorted(installed.items()):
        game = known.get(package)
        if not game:
            candidates.append({"package": package, "installed": version, "catalog": None, "reason": "not listed"})
            continue
        try:
            catalog_version = int(str(game["version"]).strip())
        except (KeyError, TypeError, ValueError):
            continue
        if version > catalog_version:
            candidates.append({"package": package, "installed": version, "catalog": catalog_version, "reason": "newer version"})
    return candidates


def main() -> None:
    parser = argparse.ArgumentParser(description="Find app versions newer than the cached library")
    parser.add_argument("--catalog", required=True, type=Path, help="Path to cached catalog.json")
    parser.add_argument("--installed", required=True, type=Path, help="Output from adb shell pm list packages --show-versioncode")
    parser.add_argument("--json", action="store_true", help="Print machine-readable candidates")
    args = parser.parse_args()
    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    if not isinstance(catalog, list):
        parser.error("catalog.json must contain a list")
    candidates = find_updates(catalog, installed_versions(args.installed.read_text(encoding="utf-8")))
    if args.json:
        print(json.dumps(candidates, indent=2))
    else:
        for item in candidates:
            previous = "missing" if item["catalog"] is None else str(item["catalog"])
            print(f"{item['package']}: installed {item['installed']}, catalog {previous}")
        print(f"{len(candidates)} candidate(s); review before contributing through the provider's approved uploader")


if __name__ == "__main__":
    main()
