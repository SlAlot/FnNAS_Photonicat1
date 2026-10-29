#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
headers="$PWD/work/kernel/header"
source_dir="$PWD/work/wireless-source"
mkdir -p dist/module/wireless dist/module/source/wireless
python3 scripts/prepare-wireless.py "$source_dir" "$PWD/dist/module/source/wireless"
make -C "$headers" M="$source_dir" ARCH=arm64 CC=gcc-12 HOSTCC=gcc-12 -j2 \
    CONFIG_ATH_COMMON=m CONFIG_ATH10K=m CONFIG_ATH10K_CE=y CONFIG_ATH10K_SDIO=m \
    CONFIG_ATH11K=m CONFIG_ATH11K_PCI=m CONFIG_QRTR=m CONFIG_QRTR_MHI=m modules
python3 scripts/verify-wireless.py "$source_dir" "$headers" dist/module/wireless/build.json
find "$source_dir" -name '*.ko' -exec cp '{}' dist/module/wireless/ ';'
python3 scripts/fetch-inputs.py iw dist/module/iw.deb
