#!/usr/bin/env python3
"""Download reviewed public vendor packages, including their CDN URL signing."""
import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import time
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('fetch_inputs', ROOT / 'scripts/fetch-inputs.py')
FETCH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FETCH)


def fetch(item, target):
    target = Path(target)
    if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() == item['sha256']:
        return
    # The official update endpoint supplies a public, short-lived CDN key.
    # Metadata is only a URL transport hint: the package must match our lock.
    query = urllib.parse.urlencode({'kv': '6.18.18-trim#491', 'tv': '1.1.31',
        'dv': '12', 'arch': 'aarch64', 'edition': 'Mainland-PE', 'platform': 'arm',
        'boot_mode': 'uboot', 'boot_brand': 'rockchip', 'boot_family': 'rk35xx',
        'boot_board': 'onethingcloud-oec'})
    with urllib.request.urlopen('https://apiv2-liveupdate.fnnas.com/?' + query, timeout=60) as response:
        metadata, _ = json.JSONDecoder().raw_decode(response.read(2 * 1024 * 1024).decode('utf-8', errors='replace'))
    candidates = [metadata.get('kernel', {})] + metadata.get('packages', [])
    candidate = next((entry for entry in candidates if entry.get('url') == item['url']), None)
    if not candidate:
        # Older pinned kernels can remain publicly downloadable after OTA advances.
        FETCH.fetch(item['url'], target, item['sha256'])
        return
    assert candidate['sign'] == item['sha256'], 'Vendor package changed; review required'
    key = bytes(value ^ 94 for value in base64.b64decode(candidate['dlkey'], validate=True))
    stamp = str(int(time.time()))
    path = urllib.parse.urlsplit(item['url']).path.encode()
    signature = hashlib.md5(key + path + stamp.encode()).hexdigest()
    url = item['url'] + '?' + urllib.parse.urlencode({'sign': signature, 't': stamp})
    FETCH.fetch(url, target, item['sha256'])


if __name__ == '__main__':
    destination = Path(sys.argv[1])
    for kernel in json.loads((ROOT / 'ota-kernels.lock.json').read_text())['kernels']:
        for kind, item in kernel['packages'].items():
            fetch(item, destination / kernel['release'] / (kind + '.deb'))
