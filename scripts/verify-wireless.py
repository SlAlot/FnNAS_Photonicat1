#!/usr/bin/env python3
"""Verify the added drivers against kernel and companion-module exports."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def verify(source, headers, expected_magic='6.18.18-trim SMP preempt mod_unload aarch64'):
    source, headers = Path(source), Path(headers)
    expected = json.loads((ROOT / 'wireless.lock.json').read_text())['modules']
    modules = list(source.rglob('*.ko'))
    assert sorted(path.stem for path in modules) == sorted(expected), 'Unexpected wireless module set'
    known = set()
    for path in (headers / 'Module.symvers', source / 'Module.symvers'):
        for line in path.read_text().splitlines():
            columns = line.split()
            if len(columns) > 1:
                known.add(columns[1])
    reports = []
    for module in modules:
        magic = subprocess.check_output(['modinfo', '-F', 'vermagic', str(module)], text=True).strip()
        assert magic == expected_magic, magic
        imports = [line.split()[1] for line in subprocess.check_output(
            ['nm', '--undefined-only', str(module)], text=True).splitlines()
            if len(line.split()) == 2 and line.split()[0] == 'U']
        assert imports and set(imports) <= known, f'Unresolved imports in {module.name}: {set(imports) - known}'
        reports.append({'name': module.name, 'sha256': hashlib.sha256(module.read_bytes()).hexdigest(),
                        'validated_imports': len(imports), 'vermagic': magic})
    return {'status': 'compiled_not_hardware_verified', 'kernel_release': expected_magic.split()[0],
            'modules': reports, 'retained_stock_stack': ['cfg80211', 'mac80211', 'mhi'],
            'modversions': False, 'abi_check': 'export_names_and_vermagic_only'}


if __name__ == '__main__':
    report = verify(*sys.argv[1:3], *sys.argv[4:5])
    Path(sys.argv[3]).write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
