#!/usr/bin/env python3
"""Inspect networking in a pristine, read-only mounted base image."""
import configparser
import json
from pathlib import Path
import subprocess
import sys

root = Path(sys.argv[1]).resolve()
packages = {}
for paragraph in (root / 'var/lib/dpkg/status').read_text().split('\n\n'):
    fields = dict(line.split(': ', 1) for line in paragraph.splitlines() if ': ' in line and not line.startswith(' '))
    name = fields.get('Package', '')
    if any(part in name for part in ('network', 'dnsmasq', 'nftables', 'iptables', 'modem', 'udev', 'systemd', 'python3')):
        packages[name] = {'version': fields.get('Version'), 'status': fields.get('Status')}
profiles = []
for path in (root / 'etc/NetworkManager/system-connections').glob('*'):
    if not path.is_file():
        continue
    config = configparser.ConfigParser(interpolation=None)
    config.read(path)
    profiles.append({'file': path.name, **{section: {key: config.get(section, key) for key in
                     ('id', 'type', 'interface-name', 'method', 'address1', 'route-metric', 'never-default')
                     if config.has_option(section, key)} for section in ('connection', 'ipv4', 'ipv6')}})
configs = {}
for pattern in ('etc/NetworkManager/NetworkManager.conf', 'etc/NetworkManager/conf.d/*.conf',
                'etc/network/interfaces', 'etc/network/interfaces.d/*'):
    for path in root.glob(pattern):
        if path.is_file():
            configs[str(path.relative_to(root))] = path.read_text(errors='replace')[:12000]
commands = {}
for command in (['nmcli', '--version'], ['dnsmasq', '--version'], ['nft', '--version'], ['iptables', '--version']):
    result = subprocess.run(['chroot', str(root), '/usr/bin/env', '-i', 'PATH=/usr/sbin:/usr/bin:/sbin:/bin',
                             *command], text=True, capture_output=True)
    commands[command[0]] = {'code': result.returncode, 'output': (result.stdout + result.stderr)[:1000]}
print(json.dumps({'packages': packages, 'profiles': profiles, 'configs': configs, 'commands': commands}, indent=2))
