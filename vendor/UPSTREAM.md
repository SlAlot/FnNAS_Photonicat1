# Source and licensing

- Base DWMAC source and private headers: Linux stable `v6.18.18`, `gregkh/linux`, under their original SPDX licenses. File identities are pinned as Git blob hashes in `sources.lock.json`.
- Legacy RK3568 XPCS/SGMII register setup: Rockchip's patch by David Wu, distributed by `photonicat/rockchip_rk3568_linux_mainline` at commit `aea14d28d6a909dec2d2154613d157d23bbc6a77`. The complete original patch is retained here.
- Adaptation: preserve the official FnNAS kernel; rebuild its Rockchip glue module from the pinned Linux source with the official board's legacy setup, explicit SGMII capability advertisement and error propagation. Only the RK3568 SGMII path gains this behavior.
- PHY reset ordering follows the official board device tree's MAC-level reset. The original PHY addresses, GPIO cells and timings are preserved.
- FnNAS images and proprietary kernel features are supplied by the FnNAS/ophub release. Their respective terms remain applicable; this repository does not contain or claim ownership of their source.

The generated module archive includes the exact modified driver source, private headers, patch and license needed for inspection. A successful compile is not hardware validation.

Factory LAN addressing uses the base image's NetworkManager 1.42, with a manual
IPv4 profile compatible with the NAS editor. A separate service supplies LAN-only
dnsmasq DHCP/DNS and scoped iptables NAT, following address changes without
resetting saved settings. See the
[NetworkManager IPv4 reference](https://networkmanager.dev/docs/api/1.42.0/settings-ipv4.html).
The profiles and physical-controller naming rules are installed offline, not
recreated by a boot-time configuration reset service.

The pinned proprietary settings bundle is adapted locally during image packaging;
its original files remain intact. Only patch instructions, not the upstream bundle,
are stored in this repository. A version-checked Nginx conf.d extension serves the
adapter and a GPU identity read from the kernel's sysfs, without disabling the
vendor's web resource recovery or authentication.

Additional wireless modules use the unmodified Linux v6.18.18 ath common,
ath10k SDIO, ath11k PCI, QRTR/MHI and Qualcomm QMI helper sources. Exact Git
blob identities and the Debian Bookworm iw package are pinned in
`wireless.lock.json`. The retained kernel's headers and stock cfg80211,
mac80211 and MHI modules are used; all imported symbols are checked. The
module source archive contains the exact build inputs and original ISC,
BSD-3-Clause-Clear and GPL-2.0 license texts.

The hotspot application uses the documented native desktop/CGI entry model:
https://developer.fnnas.com/docs/core-concepts/app-entry/ . The pinned NAS
version does not provide a working unified gateway route, so the app validates
existing local NAS administrator credentials through a narrow root helper.
Its web process runs as a dedicated unprivileged package user and never trusts
client-supplied X-Trim headers. No new administrator account is installed. Factory hotspot defaults are
provided in the app defaults.json and only seed missing settings.

The OTA adapter uses publicly downloadable, hash-pinned official FnNAS ARM64
kernel and header packages from `download.liveupdate.fnnas.com`. Their transport
URLs are obtained from the public official update endpoint; no user device ID or
credentials are sent by the builder. Package hashes, vmlinuz fingerprints and
reviewed DTBs are recorded in `ota-kernels.lock.json`. Our extra modules are built
separately for each kernel; the proprietary kernel is not replaced with a generic
distribution kernel. Kernel package hooks run before boot selection and ramdisk
generation. An isolated overlay test executes the official package's real
preinst/postinst without updating a user's system.

The native AP plugin bundles Debian Bookworm hostapd with its copyright file,
pinned by `apps/photonicat-hotspot/hostapd.lock.json`. Its optional app-private
ath11k modules use the same pinned Linux sources and official kernel headers.
`vendor/ath11k-reg905.patch` is the ImmortalWrt/OpenWrt regulatory intersection
change, pinned to ImmortalWrt commit 96f566f4a4d04ab44592314bc0279ba9e7de3de0.
The patch retains its author and license notices. Dynamic user regulatory hints
are enabled only in this app-private variant, so the requested country reaches
the firmware; firmware/BDF limits remain in force. Exact corresponding sources
and the build manifest accompany the modules inside the native package.
No cfg80211, mac80211, kernel image or firmware files are replaced by the app.

The bundled Network Panel native app is built from SlAlot/wlmb-server, branch
fnos, commit `cf7f8095d091255108088a6ac3ae22fb54084ae5`. Its Network Panel Non-Commercial Attribution License 1.0
and corresponding source accompany the firmware module source archive.
The OpenWrt main branch is not used for this integration.
