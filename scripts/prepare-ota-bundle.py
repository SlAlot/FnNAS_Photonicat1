#!/usr/bin/env python3
"""Seal each board bundle against the exact official kernel it was built for."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('dtb_patch', ROOT / 'scripts/patch-dtb.py')
DTB = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DTB)


def prepare(release, root, output):
    root, output = Path(root), Path(output)
    pin = next(k for k in json.loads((ROOT / 'ota-kernels.lock.json').read_text())['kernels'] if k['release'] == release)
    assert hashlib.sha256((root / f'boot/vmlinuz-{release}').read_bytes()).hexdigest() == pin['vmlinuz_sha256']
    report = json.loads((output / 'build.json').read_text())
    assert report['vermagic'] == pin['vermagic']
    dtb = output / 'rk3568-photonicat.dtb'
    DTB.patch(dtb, pin['original_dtb_sha256'])
    files = {'dwmac-rk.ko': report['module_sha256'], 'rk3568-photonicat.dtb': hashlib.sha256(dtb.read_bytes()).hexdigest()}
    for item in json.loads((output / 'wireless/build.json').read_text())['modules']:
        files['wireless/' + item['name']] = item['sha256']
    manifest = {'release': release, 'vmlinuz_sha256': pin['vmlinuz_sha256'], 'files': files}
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')


if __name__ == '__main__':
    prepare(*sys.argv[1:])
