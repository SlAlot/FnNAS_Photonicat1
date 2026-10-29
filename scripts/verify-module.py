#!/usr/bin/env python3
import hashlib
import json
from pathlib import Path
import subprocess
import sys

module, headers, report = map(Path, sys.argv[1:])
ROOT = Path(__file__).resolve().parents[1]
lock = json.loads((ROOT / 'sources.lock.json').read_text())
vermagic = subprocess.check_output(['modinfo', '-F', 'vermagic', str(module)], text=True).strip()
assert vermagic == '6.18.18-trim SMP preempt mod_unload aarch64', vermagic
assert subprocess.check_output(['modinfo', '-F', 'version', str(module)], text=True).strip() == '0.1.0-photonicat-test'
known = {}
for line in (headers / 'Module.symvers').read_text().splitlines():
    columns = line.split()
    if len(columns) >= 2:
        known[columns[1]] = int(columns[0], 16)
configuration = (headers / 'include/config/auto.conf').read_text().splitlines()
has_crcs = 'CONFIG_MODVERSIONS=y' in configuration
undefined = subprocess.check_output(['nm', '--undefined-only', str(module)], text=True)
imports = []
for line in undefined.splitlines():
    fields = line.split()
    if len(fields) == 2 and fields[0] == 'U':
        name = fields[1]
        assert name in known, f'Kernel does not export imported symbol: {name}'
        imports.append(name)
assert imports, 'No kernel imports found'
if has_crcs:
    versions = subprocess.check_output(['modprobe', '--show-modversions', str(module)], text=True)
    checked = []
    for line in versions.splitlines():
        crc, name = line.split()
        assert name in known and known[name] == int(crc, 16), f'Kernel symbol CRC mismatch: {name}'
        checked.append(name)
    assert checked, 'Kernel requires CRCs but the module has none'
else:
    sections = subprocess.check_output(['readelf', '--section-headers', '--wide', str(module)], text=True)
    assert '__versions' not in sections, 'Module CRC mode differs from the target kernel'
result = {'kernel_release': lock['target']['kernel_release'], 'vermagic': vermagic,
          'module_sha256': hashlib.sha256(module.read_bytes()).hexdigest(),
          'validated_imports': len(imports), 'modversions': has_crcs,
          'abi_check': 'symbol_crcs' if has_crcs else 'export_names_and_vermagic_only',
          'status': 'compiled_not_hardware_verified'}
report.write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps(result, indent=2))
