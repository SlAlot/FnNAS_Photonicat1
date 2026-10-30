#!/usr/bin/env python3
"""Build the exact pinned FnNAS network panel source."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def run(*args, **kwargs):return subprocess.run(args,check=True,**kwargs)


def build(source, output):
    source,output = Path(source).resolve(),Path(output).resolve()
    pin=json.loads((ROOT/'panel.lock.json').read_text())
    commit=subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True).strip()
    assert commit==pin['commit'], 'Unexpected panel source revision'
    run('git','-C',str(source),'diff','--quiet')
    run('cargo','build','--manifest-path',str(source/'backend/Cargo.toml'),'--locked','--release',
        '--features','fnos','--bin','wlmb-fnos','--target','aarch64-unknown-linux-musl')
    spec=importlib.util.spec_from_file_location('panel_prepare',source/'tools/prepare-fnos.py')
    prepare=importlib.util.module_from_spec(spec);spec.loader.exec_module(prepare)
    prepare.prepare(source/'backend/target/aarch64-unknown-linux-musl/release/wlmb-fnos',output)
    assert 'version='+pin['native_version'] in (output/'manifest').read_text()
    # Keep the exact source beside the firmware module source distribution.
    corresponding=output.parent/'source/wlmb-fnos'
    shutil.copytree(source,corresponding,dirs_exist_ok=True,
        ignore=shutil.ignore_patterns('.git','target','dist','node_modules','__pycache__','*.pyc'))
    (output/'build.json').write_text(json.dumps(pin,indent=2)+'\n')


if __name__=='__main__':build(*sys.argv[1:])
