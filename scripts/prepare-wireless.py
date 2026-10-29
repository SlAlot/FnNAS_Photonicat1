#!/usr/bin/env python3
"""Fetch exact in-tree wireless sources for the retained FnNAS kernel."""
import concurrent.futures
import importlib.util
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('fetch_inputs', ROOT / 'scripts/fetch-inputs.py')
FETCH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FETCH)


def prepare(destination, snapshot=None):
    destination = Path(destination)
    lock = json.loads((ROOT / 'wireless.lock.json').read_text())
    def download(item):
        url = f"https://raw.githubusercontent.com/gregkh/linux/{lock['linux_commit']}/{item['source']}"
        FETCH.fetch(url, destination / item['path'], item['blob'], True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(download, lock['files']))
    # Kbuild owns only these additional modules; existing cfg80211, mac80211
    # and MHI modules and their kernel configuration are retained unchanged.
    (destination / 'Makefile').write_text('obj-m += ath/ qrtr/ qmi/\n'
        'subdir-ccflags-y += -DCONFIG_ATH10K_CE=1 -DCONFIG_ATH_COMMON_MODULE=1 '
        '-DCONFIG_ATH10K_MODULE=1 -DCONFIG_ATH10K_SDIO_MODULE=1 '
        '-DCONFIG_ATH11K_MODULE=1 -DCONFIG_ATH11K_PCI_MODULE=1 '
        '-DCONFIG_QRTR_MODULE=1 -DCONFIG_QRTR_MHI_MODULE=1 '
        '-DCONFIG_QCOM_QMI_HELPERS_MODULE=1\n', encoding='utf-8', newline='\n')
    (destination / 'qmi/Makefile').write_text('obj-m += qmi_helpers.o\n'
        'qmi_helpers-y := qmi_encdec.o qmi_interface.o\n', encoding='utf-8', newline='\n')
    if snapshot:
        snapshot = Path(snapshot)
        for relative in sorted({item['path'] for item in lock['files']} | {'Makefile', 'qmi/Makefile'}):
            target = snapshot / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(destination / relative, target)
        shutil.copyfile(ROOT / 'wireless.lock.json', snapshot / 'wireless.lock.json')


if __name__ == '__main__':
    prepare(*sys.argv[1:])
