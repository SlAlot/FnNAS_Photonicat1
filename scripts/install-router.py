#!/usr/bin/env python3
"""Install factory profiles offline; never run this against a live router."""
from pathlib import Path
import json
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]


def install(root):
    root = Path(root).resolve()
    assert root != Path('/') and (root / 'etc/os-release').is_file(), 'Expected a mounted image root'
    for package in ('network-manager', 'dnsmasq-base', 'iptables'):
        status = (root / 'var/lib/dpkg/status').read_text()
        assert any(f'Package: {package}\n' in paragraph and 'Status: install ok installed' in paragraph
                   for paragraph in status.split('\n\n')), f'Missing router dependency: {package}'
    copied = []
    for source in sorted((ROOT / 'files').rglob('*')):
        if not source.is_file() or '__pycache__' in source.parts:
            continue
        relative = source.relative_to(ROOT / 'files')
        target = root / relative
        assert not target.exists() and not target.is_symlink(), f'Refusing to overwrite upstream settings: {relative}'
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        mode = 0o600 if source.suffix == '.nmconnection' else 0o644
        if 'initramfs-tools/hooks' in relative.as_posix():
            mode = 0o755
        if relative.parts[:2] == ('etc', 'kernel'):
            mode = 0o755
        if relative.parts[:3] == ('etc', 'NetworkManager', 'dispatcher.d'):
            mode = 0o755
        if relative.parts[:3] == ('usr', 'local', 'sbin'):
            mode = 0o755
        if relative.parts[:2] == ('etc', 'sudoers.d'):
            mode = 0o440
        target.chmod(mode)
        copied.append(str(relative))
    wants = root / 'etc/systemd/system/multi-user.target.wants'
    wants.mkdir(parents=True, exist_ok=True)
    (wants / 'photonicat-router.service').symlink_to('../photonicat-router.service')
    (wants / 'photonicat-hardware.service').symlink_to('../photonicat-hardware.service')
    (wants / 'photonicat-hotspot-install.service').symlink_to('../photonicat-hotspot-install.service')
    (wants / 'photonicat-panel-install.service').symlink_to('../photonicat-panel-install.service')
    (wants / 'photonicat-kernel.service').symlink_to('../photonicat-kernel.service')
    return {'lan': {'interface': 'pcatlan0', 'controller': 'fe2a0000.ethernet', 'address': '192.168.17.1/24',
                    'dhcp': True, 'dns_forwarding': True, 'nat': 'photonicat-router'},
            'wired_wan': {'interface': 'pcatwan0', 'controller': 'fe010000.ethernet', 'metric': 600},
            'modem': {'drivers': ['cdc_ether', 'rndis_host', 'cdc_ncm'], 'metric': 100},
            'installed_files': copied}


if __name__ == '__main__':
    print(json.dumps(install(sys.argv[1]), indent=2))
