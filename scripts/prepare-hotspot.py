#!/usr/bin/env python3
"""Stage the native fnOS app; no SSID, password or account is preconfigured."""
from pathlib import Path
import math
import shutil
import struct
import sys
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
            distance = math.hypot(px - 32, py - 49)
            white = distance < 3 or (py < 44 and abs(px - 32) < 23 and
                any(abs(distance - radius) < 1.7 for radius in (11, 21, 31)))
            rows.extend((255, 255, 255) if white else (52, 133, 255))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!IIBBBBB', size, size, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(rows)) + chunk(b'IEND', b''))


def prepare(destination):
    destination = Path(destination)
    shutil.copytree(ROOT / 'apps/photonicat-hotspot', destination, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    for size, name in ((64, 'ICON.PNG'), (256, 'ICON_256.PNG')):
        (destination / name).write_bytes(icon(size))
    for hook in ('install_init', 'install_callback', 'uninstall_init', 'uninstall_callback',
                 'upgrade_init', 'upgrade_callback', 'config_init', 'config_callback'):
        (destination / 'cmd' / hook).write_text('#!/bin/sh\nexit 0\n', encoding='utf-8', newline='\n')
    for path in (destination / 'cmd').iterdir():
        path.chmod(0o755)
    (destination / 'app/ui/index.cgi').chmod(0o755)


if __name__ == '__main__':
    prepare(sys.argv[1])
