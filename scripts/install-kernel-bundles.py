#!/usr/bin/env python3
"""Package the base and reviewed OTA board fixes outside vendor-managed paths."""
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]


def install(root, modules):
    root, modules = Path(root), Path(modules)
    release = json.loads((ROOT / 'sources.lock.json').read_text())['target']['kernel_release']
    base = root / 'usr/lib/photonicat-adaptation/kernels'
    output = base / release
    output.mkdir(parents=True, exist_ok=True)
    shutil.copy2(modules / 'dwmac-rk.ko', output / 'dwmac-rk.ko')
    shutil.copy2(root / 'boot/dtb/rockchip/rk3568-photonicat.dtb', output / 'rk3568-photonicat.dtb')
    shutil.copytree(modules / 'wireless', output / 'wireless')
    files = {str(path.relative_to(output)): hashlib.sha256(path.read_bytes()).hexdigest()
             for path in output.rglob('*') if path.is_file() and path.suffix in ('.ko', '.dtb')}
    manifest = {'release': release, 'vmlinuz_sha256': hashlib.sha256((root / f'boot/vmlinuz-{release}').read_bytes()).hexdigest(), 'files': files}
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    # Also fix the source used by the vendor's 10-sync-dtb kernel hook.
    shutil.copy2(output / 'rk3568-photonicat.dtb', root / f'usr/lib/linux-image-{release}/rockchip/rk3568-photonicat.dtb')
    for pin in json.loads((ROOT / 'ota-kernels.lock.json').read_text())['kernels']:
        directory = modules / 'ota' / pin['release']
        data = json.loads((directory / 'manifest.json').read_text())
        assert data['vmlinuz_sha256'] == pin['vmlinuz_sha256']
        for relative, expected in data['files'].items():
            assert hashlib.sha256((directory / relative).read_bytes()).hexdigest() == expected
        shutil.copytree(directory, base / pin['release'])


if __name__ == '__main__':
    install(*sys.argv[1:])
