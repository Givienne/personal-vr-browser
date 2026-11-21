#!/usr/bin/env python3
"""Load a local game-list snapshot into the catalog cache."""

import argparse
from pathlib import Path

from app import parse_gamelist, write_catalog


def main():
    parser = argparse.ArgumentParser(description="Import game-list metadata")
    parser.add_argument("file", type=Path, help="Semicolon-delimited GameList.txt")
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    args = parser.parse_args()

    games, skipped = parse_gamelist(args.file.read_text(encoding="utf-8-sig"))
    if not games:
        parser.error("No valid games found; catalog left unchanged")
    write_catalog(games, args.data_dir)
    print(f"Imported {len(games)} games ({skipped} skipped)")


if __name__ == "__main__":
    main()
