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
versions = subprocess.check_output(['modprobe', '--show-modversions', str(module)], text=True)
imports = []
for line in versions.splitlines():
    crc, name = line.split()
    assert name in known and known[name] == int(crc, 16), f'Kernel symbol CRC mismatch: {name}'
    imports.append(name)
assert imports, 'No versioned imports found'
result = {'kernel_release': lock['target']['kernel_release'], 'vermagic': vermagic,
          'module_sha256': hashlib.sha256(module.read_bytes()).hexdigest(),
          'validated_imports': len(imports), 'status': 'compiled_not_hardware_verified'}
report.write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps(result, indent=2))
