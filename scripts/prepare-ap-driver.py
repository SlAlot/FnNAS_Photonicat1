#!/usr/bin/env python3
"""Build an app-private ath11k variant against the retained official headers."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
PATCH_SHA = '8f405ce58e89f62ce4fe282fc7062dbe839f33e61f6cd8a4cd4a7d70773fb90c'
MARKER = '0.2.0-reg905'


def build(headers, modules, destination, source):
    headers, modules, destination, source = map(Path,(headers, modules, destination, source))
    spec = importlib.util.spec_from_file_location('wireless_inputs',ROOT/'scripts/prepare-wireless.py')
    inputs = importlib.util.module_from_spec(spec);spec.loader.exec_module(inputs)
    inputs.prepare(source)
    with (source/'Makefile').open('a') as stream:
        stream.write('subdir-ccflags-y += -DCONFIG_ATH_REG_DYNAMIC_USER_REG_HINTS=1\n')
    patch = ROOT/'vendor/ath11k-reg905.patch'
    assert hashlib.sha256(patch.read_bytes()).hexdigest()==PATCH_SHA
    subprocess.run(['git','apply','-p4','--unsafe-paths','--directory='+str(source.resolve()),str(patch.resolve())],check=True)
    core = source/'ath/ath11k/core.c'
    with core.open('a') as stream:
        stream.write('\nstatic char *photonicat_plugin_build = "'+MARKER+'";\n'
                     'module_param(photonicat_plugin_build, charp, 0444);\n'
                     'MODULE_PARM_DESC(photonicat_plugin_build, "Photonicat AP plugin module identity");\n')
    subprocess.run(['make','-C',str(headers.resolve()),'M='+str(source.resolve()),'ARCH=arm64',
                    'CC=gcc-12','HOSTCC=gcc-12','-j2','CONFIG_ATH_COMMON=m','CONFIG_ATH10K=m',
                    'CONFIG_ATH10K_CE=y','CONFIG_ATH10K_SDIO=m','CONFIG_ATH11K=m','CONFIG_ATH11K_PCI=m',
                    'CONFIG_QRTR=m','CONFIG_QRTR_MHI=m','modules'],check=True)
    release = (headers/'include/config/kernel.release').read_text().strip()
    report = {'kernel_release':release,'marker':MARKER,'sha256':{},'stack_sha256':{},'patch_sha256':PATCH_SHA}
    destination = destination/release;destination.mkdir(parents=True,exist_ok=True)
    for name in ('cfg80211','mac80211'):
        paths = list(modules.rglob(name+'.ko'))
        assert len(paths)==1, f'Expected one official {name} module'
        report['stack_sha256'][name]=hashlib.sha256(paths[0].read_bytes()).hexdigest()
    for name in ('ath11k','ath11k_pci'):
        path = source/'ath/ath11k'/(name+'.ko')
        magic = subprocess.check_output(['modinfo','-F','vermagic',str(path)],text=True).strip()
        assert magic.split()[0]==release
        if 'vermagic' in report:assert report['vermagic']==magic
        report['vermagic']=magic
        report['sha256'][name]=hashlib.sha256(path.read_bytes()).hexdigest()
        shutil.copy2(path,destination/path.name)
    # Verify all imports using the same kernel and companion exports as the stock build.
    spec = importlib.util.spec_from_file_location('wireless_verify',ROOT/'scripts/verify-wireless.py')
    verify = importlib.util.module_from_spec(spec);spec.loader.exec_module(verify)
    report['abi']=verify.verify(source,headers,report['vermagic'])
    (destination/'build.json').write_text(json.dumps(report,indent=2)+'\n')
    package_source=destination/'source'
    if package_source.exists():shutil.rmtree(package_source)
    shutil.copytree(source,package_source,ignore=shutil.ignore_patterns('*.o','*.ko','*.cmd','*.mod','*.mod.c',
                    'Module.symvers','modules.order','.tmp_versions'))
    shutil.copy2(patch,package_source/'ath11k-reg905.patch')
    shutil.copy2(ROOT/'wireless.lock.json',package_source/'wireless.lock.json')
    shutil.copy2(ROOT/'LICENSE',package_source/'LICENSE')


if __name__=='__main__':build(*sys.argv[1:])
