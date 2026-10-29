#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 scripts/fetch-ota-kernel.py work/ota
cp ota-kernels.lock.json dist/module/source/ota-kernels.lock.json
while IFS= read -r release; do
    work_dir="$PWD/work/ota/$release"
    output="$PWD/dist/module/ota/$release"
    mkdir -p "$work_dir/root" "$work_dir/driver" "$output/wireless" dist/module/ota-inputs
    dpkg-deb -x "$work_dir/image.deb" "$work_dir/root"
    dpkg-deb -x "$work_dir/headers.deb" "$work_dir/root"
    headers="$work_dir/root/usr/src/linux-headers-$release"
    cp work/driver/*.c work/driver/*.h work/driver/Makefile "$work_dir/driver/"
    make -C "$headers" M="$work_dir/driver" ARCH=arm64 CC=gcc-12 HOSTCC=gcc-12 -j2 modules
    original="$work_dir/root/lib/modules/$release/kernel/drivers/net/ethernet/stmicro/stmmac/dwmac-rk.ko"
    magic="$(modinfo -F vermagic "$original" | sed 's/ *$//')"
    python3 scripts/verify-module.py "$work_dir/driver/dwmac-rk.ko" "$headers" "$output/build.json" "$magic"
    cp "$work_dir/driver/dwmac-rk.ko" "$output/"
    python3 scripts/prepare-wireless.py "$work_dir/wireless"
    make -C "$headers" M="$work_dir/wireless" ARCH=arm64 CC=gcc-12 HOSTCC=gcc-12 -j2 \
        CONFIG_ATH_COMMON=m CONFIG_ATH10K=m CONFIG_ATH10K_CE=y CONFIG_ATH10K_SDIO=m \
        CONFIG_ATH11K=m CONFIG_ATH11K_PCI=m CONFIG_QRTR=m CONFIG_QRTR_MHI=m modules
    python3 scripts/verify-wireless.py "$work_dir/wireless" "$headers" "$output/wireless/build.json" "$magic"
    find "$work_dir/wireless" -name '*.ko' -exec cp '{}' "$output/wireless/" ';'
    cp "$work_dir/root/usr/lib/linux-image-$release/rockchip/rk3568-photonicat.dtb" "$output/"
    python3 scripts/prepare-ota-bundle.py "$release" "$work_dir/root" "$output"
    cp "$work_dir/image.deb" "dist/module/ota-inputs/$release.deb"
done < <(python3 -c 'import json; print("\n".join(k["release"] for k in json.load(open("ota-kernels.lock.json"))["kernels"]))')
