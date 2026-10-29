# Source and licensing

- Base DWMAC source and private headers: Linux stable `v6.18.18`, `gregkh/linux`, under their original SPDX licenses. File identities are pinned as Git blob hashes in `sources.lock.json`.
- Legacy RK3568 XPCS/SGMII register setup: Rockchip's patch by David Wu, distributed by `photonicat/rockchip_rk3568_linux_mainline` at commit `aea14d28d6a909dec2d2154613d157d23bbc6a77`. The complete original patch is retained here.
- Adaptation: preserve the official FnNAS kernel; rebuild its Rockchip glue module from the pinned Linux source with the official board's legacy setup, explicit SGMII capability advertisement and error propagation. Only the RK3568 SGMII path gains this behavior.
- PHY reset ordering follows the official board device tree's MAC-level reset. The original PHY addresses, GPIO cells and timings are preserved.
- FnNAS images and proprietary kernel features are supplied by the FnNAS/ophub release. Their respective terms remain applicable; this repository does not contain or claim ownership of their source.

The generated module archive includes the exact modified driver source, private headers, patch and license needed for inspection. A successful compile is not hardware validation.
