#!/usr/bin/env python3
"""Stage the native fnOS AP app with first-install hotspot defaults."""
from pathlib import Path
import math
import hashlib
import io
import json
import shutil
import struct
import sys
import tarfile
import urllib.request
import zlib

ROOT = Path(__file__).resolve().parents[1]


def icon(size):
    def chunk(kind, payload):
        return struct.pack('!I', len(payload)) + kind + payload + struct.pack('!I', zlib.crc32(kind + payload))
    rows = bytearray()
    for y in range(size):
        rows.append(0)
        for x in range(size):
            px, py = x * 64 / size, y * 64 / size
            distance = math.hypot(px - 32, py - 32)
            angle = math.atan2(py - 32, px - 32)
            edge = 23 if math.cos(8 * angle) > 0 else 18
            white = 8 <= distance <= edge
            rows.extend((255, 255, 255) if white else (52, 133, 255))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!IIBBBBB', size, size, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(rows)) + chunk(b'IEND', b''))


def prepare(destination, drivers=None):
    destination = Path(destination)
    shutil.copytree(ROOT / 'apps/photonicat-hotspot', destination, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    for size, name in ((64, 'ICON.PNG'), (256, 'ICON_256.PNG')):
        data = icon(size)
        (destination / name).write_bytes(data)
        (destination / 'app/ui/images' / f'icon_{size}.png').write_bytes(data)
        (destination / 'app/ui/images' / f'settings-v2_{size}.png').write_bytes(data)
    pin = json.loads((ROOT/'apps/photonicat-hotspot/hostapd.lock.json').read_text())
    cache = ROOT/'work'/Path(pin['url']).name
    cache.parent.mkdir(exist_ok=True)
    if not cache.exists():
        cache.write_bytes(urllib.request.urlopen(pin['url'],timeout=60).read())
    data = cache.read_bytes()
    assert hashlib.sha256(data).hexdigest()==pin['sha256'], 'Unexpected hostapd package'
    assert data[:8]==b'!<arch>\n'
    position=8; archive=None
    while position+60<=len(data):
        header=data[position:position+60];size=int(header[48:58]);name=header[:16].decode().strip().rstrip('/')
        payload=data[position+60:position+60+size]
        if name.startswith('data.tar'):
            archive=tarfile.open(fileobj=io.BytesIO(payload),mode='r:*');break
        position+=60+size+(size%2)
    assert archive is not None
    (destination/'app/bin').mkdir(exist_ok=True)
    (destination/'app/licenses').mkdir(exist_ok=True)
    for member,target in (('./usr/sbin/hostapd',destination/'app/bin/hostapd'),
                          ('./usr/share/doc/hostapd/copyright',destination/'app/licenses/hostapd-copyright')):
        target.write_bytes(archive.extractfile(member).read())
    (destination/'app/bin/hostapd').chmod(0o755)
    if drivers:
        drivers=Path(drivers)
        assert list(drivers.glob('*/build.json')), 'Missing app-private driver builds'
        for report in drivers.glob('*/build.json'):
            pin=json.loads(report.read_text())
            for name,digest in pin['sha256'].items():
                assert hashlib.sha256((report.parent/(name+'.ko')).read_bytes()).hexdigest()==digest
        shutil.copytree(drivers,destination/'app/drivers',dirs_exist_ok=True)
    for hook in ('install_init', 'install_callback', 'uninstall_callback',
                 'upgrade_callback', 'config_init', 'config_callback'):
        (destination / 'cmd' / hook).write_text('#!/bin/sh\nexit 0\n', encoding='utf-8', newline='\n')
    for hook in ('uninstall_init','upgrade_init'):
        (destination/'cmd'/hook).write_text('#!/bin/sh\nexec "$(dirname "$0")/main" stop\n',encoding='utf-8',newline='\n')
    (destination/'wizard').mkdir(exist_ok=True)
    for path in (destination / 'cmd').iterdir():
        path.chmod(0o755)
    (destination / 'app/ui/index.cgi').chmod(0o755)


if __name__ == '__main__':
    prepare(*sys.argv[1:])
