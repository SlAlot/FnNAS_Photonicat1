#!/usr/bin/env python3
"""Use the official board's MAC-level PHY reset before MDIO identification."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
MACS = ('/ethernet@fe010000', '/ethernet@fe2a0000')

def command(*args):
    return subprocess.check_output(args, text=True).strip()

def patch(path, expected):
    path = Path(path)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == expected, 'Unexpected upstream DTB; review required'
    assert 'ariaboard,photonicat' in command('fdtget', '-ts', str(path), '/', 'compatible')
    before = {}
    for mac in MACS:
        phy = mac + '/mdio/ethernet-phy@3'
        assert command('fdtget', '-tx', str(path), phy, 'reg') == '3'
        cells = command('fdtget', '-tx', str(path), phy, 'reset-gpios').split()
        assert len(cells) == 3 and cells[-1] == '1', 'Expected active-low PHY reset'
        assertion = command('fdtget', '-tx', str(path), phy, 'reset-assert-us')
        deassertion = command('fdtget', '-tx', str(path), phy, 'reset-deassert-us')
        before[mac] = {'phy_address': 3, 'gpio': cells, 'assert_us_hex': assertion, 'deassert_us_hex': deassertion}
        subprocess.run(['fdtput', '-tx', str(path), mac, 'snps,reset-gpio', *cells], check=True)
        subprocess.run(['fdtput', str(path), mac, 'snps,reset-active-low'], check=True)
        subprocess.run(['fdtput', '-tx', str(path), mac, 'snps,reset-delays-us', '0', assertion, deassertion], check=True)
        for key in ('reset-gpios', 'reset-assert-us', 'reset-deassert-us'):
            subprocess.run(['fdtput', '-d', str(path), phy, key], check=True)
        assert command('fdtget', '-tx', str(path), mac, 'snps,reset-gpio').split() == cells
        assert command('fdtget', '-tx', str(path), phy, 'reg') == '3'
    return {'original_sha256': expected, 'patched_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'reset_changes': before}

if __name__ == '__main__':
    lock = json.loads((ROOT / 'sources.lock.json').read_text())
    report = patch(sys.argv[1], lock['target']['original_dtb_sha256'])
    Path(sys.argv[2]).write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
