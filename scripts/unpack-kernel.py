#!/usr/bin/env python3
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
lock = json.loads((ROOT / 'sources.lock.json').read_text())
archive, destination = Path(sys.argv[1]), Path(sys.argv[2])
assert hashlib.sha256(archive.read_bytes()).hexdigest() == lock['target']['kernel']['sha256']
destination.mkdir(parents=True, exist_ok=True)
with tarfile.open(archive) as outer:
    for member in outer:
        if not member.isfile() or not member.name.endswith('.tar.gz'):
            continue
        name = Path(member.name).name
        kind = next((prefix for prefix in ('header', 'dtb', 'boot', 'modules') if name.startswith(prefix + '-')), None)
        assert kind, name
        target = destination / kind
        target.mkdir(exist_ok=True)
        with tarfile.open(fileobj=io.BytesIO(outer.extractfile(member).read())) as inner:
            # These two links point into the installed system, not the build
            # workspace. They are unnecessary for building the glue module.
            runtime_links = {
                '6.18.18-trim/updates/trim/alternatives': '/usr/trim/modules/6.18.18-trim/alternatives',
                '6.18.18-trim/build': '/usr/src/linux-headers-6.18.18-trim',
            } if kind == 'modules' else {}
            members = []
            for item in inner:
                if item.issym() and runtime_links.get(item.name) == item.linkname:
                    continue
                members.append(item)
            # All other entries retain path, device and link safety checks.
            inner.extractall(target, members=members, filter='data')
print(f'Verified and extracted kernel inputs to {destination}')
