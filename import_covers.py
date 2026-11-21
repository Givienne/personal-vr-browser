#!/usr/bin/env python3
"""Safely import CyberDeck .meta/thumbnails/*.jpg into Vialenne VR's covers.

No source credentials required. Only known package names from catalog.json are
accepted; members are copied as bytes without extracting paths or links.
"""
import json
import os
import pwd
from pathlib import Path, PurePosixPath
import sys
import tarfile

ROOT = Path('/srv/storage/vr-library')
LIMIT = 6 * 1024 * 1024
if len(sys.argv) != 2:
    sys.exit('Usage: sudo python3 import_covers.py ~/vialenne-thumbnails.tar.gz')
source = Path(sys.argv[1])
known = set()
for item in json.loads((ROOT/'catalog.json').read_text('utf-8')):
    if isinstance(item,dict) and isinstance(item.get('id'),str):
        known.add(item['id'])
user = pwd.getpwnam('vialenne-vr')
covers = ROOT/'covers'
covers.mkdir(mode=0o755, exist_ok=True)
count=0
skipped=0
with tarfile.open(source,'r:gz') as archive:
    for member in archive:
        path=PurePosixPath(member.name)
        if (not member.isfile() or len(path.parts)!=2 or path.parts[0]!='thumbnails'
            or path.suffix.lower()!='.jpg' or path.stem not in known
            or not 0 < member.size <= LIMIT):
            skipped+=1
            continue
        inp=archive.extractfile(member)
        if inp is None: continue
        dest=covers / (path.stem+'.jpg')
        # O_NOFOLLOW prevents a pre-existing symlink from redirecting writes.
        fd=os.open(dest,os.O_CREAT|os.O_WRONLY|os.O_TRUNC|os.O_NOFOLLOW,0o644)
        try:
            with os.fdopen(fd,'wb') as out:
                remaining=member.size
                while remaining:
                    buf=inp.read(min(1024*1024, remaining))
                    if not buf: raise ValueError('Truncated tar member')
                    out.write(buf)
                    remaining-=len(buf)
            os.chmod(dest,0o644)
            os.chown(dest,user.pw_uid,user.pw_gid)
            count+=1
        finally:
            inp.close()
print(f'Imported {count} matching covers; skipped {skipped} archive entries')
