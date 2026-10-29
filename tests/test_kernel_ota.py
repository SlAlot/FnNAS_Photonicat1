import hashlib
import json
from pathlib import Path
import runpy
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
HELPER = runpy.run_path(str(ROOT / 'files/usr/local/sbin/photonicat-kernel-adapt'))


class KernelUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.release = '6.18.18.c1145-trim'
        self.base = self.root / 'bundles'
        self.bundle = self.base / self.release
        self.bundle.mkdir(parents=True)
        files = {'dwmac-rk.ko': b'fixed driver', 'rk3568-photonicat.dtb': b'fixed dtb',
                 'wireless/ath11k_pci.ko': b'fixed wifi'}
        for name, data in files.items():
            path = self.bundle / name
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(data)
        self.manifest = {'release': self.release, 'vmlinuz_sha256': hashlib.sha256(b'kernel').hexdigest(),
                         'files': {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}}
        (self.bundle / 'manifest.json').write_text(json.dumps(self.manifest))
        (self.root / 'boot').mkdir()
        (self.root / f'boot/vmlinuz-{self.release}').write_bytes(b'kernel')
        self.driver = self.root / f'lib/modules/{self.release}/kernel/drivers/net/ethernet/stmicro/stmmac/dwmac-rk.ko'
        self.driver.parent.mkdir(parents=True)
        self.driver.write_bytes(b'vendor overwritten driver')
        self.profile = self.root / 'etc/NetworkManager/system-connections/user.nmconnection'
        self.profile.parent.mkdir(parents=True)
        self.profile.write_bytes(b'custom user profile')

    def test_update_restores_driver_both_dtb_copies_without_changing_profiles(self):
        HELPER['apply'](self.root, self.base, self.release, depmod=False)
        self.assertEqual(self.driver.read_bytes(), b'fixed driver')
        for path in ('boot/dtb/rockchip/rk3568-photonicat.dtb',
                     f'usr/lib/linux-image-{self.release}/rockchip/rk3568-photonicat.dtb'):
            self.assertEqual((self.root / path).read_bytes(), b'fixed dtb')
        self.assertEqual(self.profile.read_bytes(), b'custom user profile')
        # A second package reinstallation is corrected as well.
        self.driver.write_bytes(b'vendor overwritten driver')
        HELPER['apply'](self.root, self.base, self.release, depmod=False)
        self.assertEqual(self.driver.read_bytes(), b'fixed driver')

    def test_changed_kernel_is_rejected_before_any_writes(self):
        (self.root / f'boot/vmlinuz-{self.release}').write_bytes(b'another ABI with the same release')
        with self.assertRaises(ValueError):
            HELPER['apply'](self.root, self.base, self.release, depmod=False)
        self.assertEqual(self.driver.read_bytes(), b'vendor overwritten driver')
        self.assertFalse((self.root / 'boot/dtb').exists())

    def test_corrupt_bundle_and_unreviewed_kernel_rejected(self):
        (self.bundle / 'wireless/ath11k_pci.ko').write_bytes(b'corrupt')
        with self.assertRaises(ValueError):
            HELPER['apply'](self.root, self.base, self.release, depmod=False)
        self.assertEqual(self.driver.read_bytes(), b'vendor overwritten driver')
        with self.assertRaises(FileNotFoundError):
            HELPER['checked_bundle'](self.base, '6.19.0-unknown')

    def test_hooks_precede_boot_selection_and_initramfs(self):
        hooks = ROOT / 'files/etc/kernel'
        self.assertIn('check "$1"', (hooks / 'preinst.d/05-photonicat-compatible').read_text())
        self.assertIn('apply "$1"', (hooks / 'postinst.d/12-photonicat-adapt').read_text())
        self.assertTrue('10-sync-dtb' < '12-photonicat-adapt' < '15-update-ukernel-ver' < 'initramfs-tools')
