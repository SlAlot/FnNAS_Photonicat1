#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
test "$(uname -m)" = aarch64 || { echo 'Use an ARM64 Linux build runner'; exit 1; }
mkdir -p work dist/module/source
python3 scripts/fetch-inputs.py kernel work/kernel.tar.gz
python3 scripts/unpack-kernel.py work/kernel.tar.gz work/kernel
python3 scripts/fetch-inputs.py sources work/driver
python3 scripts/prepare-driver.py work/driver/dwmac-rk.c
make -C "$PWD/work/kernel/header" M="$PWD/work/driver" ARCH=arm64 CC=gcc-12 HOSTCC=gcc-12 -j2 modules
python3 scripts/verify-module.py work/driver/dwmac-rk.ko work/kernel/header dist/module/build.json
cp work/driver/dwmac-rk.ko dist/module/
cp work/driver/*.c work/driver/*.h work/driver/*.patch work/driver/Makefile dist/module/source/
cp LICENSE vendor/UPSTREAM.md vendor/rockchip-sgmii.patch dist/module/source/
cp sources.lock.json dist/module/
sha256sum dist/module/dwmac-rk.ko
