#!/usr/bin/env python3
"""Exercise the official kernel package's real hooks in an isolated overlay."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def inside(release):
    base = Path('/usr/lib/photonicat-adaptation/kernels') / release
    manifest = json.loads((base / 'manifest.json').read_text())
    profile = Path('/etc/NetworkManager/system-connections/photonicat-lan.nmconnection')
    content = profile.read_text().replace('192.168.17.1/24', '192.168.27.1/24')
    profile.write_text(content)
    env = {**os.environ, 'PCAT_IMAGE_BUILD': '1', 'DEBIAN_FRONTEND': 'noninteractive'}
    # No synthetic postinst: execute the official package's preinst/postinst,
    # including DTB sync, board hook, depmod and mkinitramfs.
    run('dpkg', '-i', '/tmp/ota-kernel.deb', env=env)
    assert profile.read_text() == content, 'Kernel update reset a saved LAN profile'
    module = Path(f'/lib/modules/{release}/kernel/drivers/net/ethernet/stmicro/stmmac/dwmac-rk.ko')
    assert hashlib.sha256(module.read_bytes()).hexdigest() == manifest['files']['dwmac-rk.ko']
    initrd = Path(f'/boot/initrd.img-{release}')
    assert initrd.is_file(), 'Official postinst did not generate the new initramfs'
    run('unmkinitramfs', str(initrd), '/tmp/new-initramfs')
    early = list(Path('/tmp/new-initramfs').rglob('dwmac-rk.ko'))
    assert early and all(hashlib.sha256(path.read_bytes()).hexdigest() == manifest['files']['dwmac-rk.ko'] for path in early), 'OTA initramfs has stale board drivers'
    assert list(Path('/tmp/new-initramfs').rglob('70-photonicat-net.rules')), 'OTA lost early LAN naming rules'
    for relative in ('boot/dtb/rockchip/rk3568-photonicat.dtb',
                     f'usr/lib/linux-image-{release}/rockchip/rk3568-photonicat.dtb'):
        assert hashlib.sha256(Path('/' + relative).read_bytes()).hexdigest() == manifest['files']['rk3568-photonicat.dtb']
    print('Official kernel OTA hooks, driver, DTB, early boot and saved LAN profile verified', flush=True)


def sandbox(root, directory, package, release):
    assert os.environ.get('PCAT_OTA_SANDBOX') == '1'
    run('mount', '--make-rprivate', '/')
    root, directory = Path(root).resolve(), Path(directory).resolve()
    for name in ('upper', 'overlay-work', 'root'):
        (directory / name).mkdir()
    merged = directory / 'root'
    run('mount', '-t', 'overlay', 'overlay', '-o',
        f'lowerdir={root},upperdir={directory}/upper,workdir={directory}/overlay-work', str(merged))
    for name in ('run', 'tmp'):
        run('mount', '-t', 'tmpfs', 'tmpfs', str(merged / name))
    run('mount', '-t', 'proc', 'proc', str(merged / 'proc'))
    shutil.copy2(__file__, merged / 'tmp/kernel-ota-integration.py')
    shutil.copy2(package, merged / 'tmp/ota-kernel.deb')
    run('chroot', str(merged), '/usr/bin/env', '-i',
        'PATH=/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL=C', 'HOME=/root',
        'python3', '/tmp/kernel-ota-integration.py', '--inside', release)


if __name__ == '__main__':
    if sys.argv[1] == '--inside':
        inside(sys.argv[2])
    elif sys.argv[1] == '--sandbox':
        sandbox(*sys.argv[2:])
    else:
        raise SystemExit('Only run in the build-only sandbox')
