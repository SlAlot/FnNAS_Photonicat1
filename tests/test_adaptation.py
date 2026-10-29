import hashlib
import importlib.util
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

class DriverTests(unittest.TestCase):
    def setUp(self):
        self.module = load('prepare-driver')
        self.original = (ROOT / 'work/source/dwmac-rk.c').read_text()

    def test_upstream_drift_is_rejected(self):
        with self.assertRaises(ValueError):
            self.module.prepare(self.original.replace('static void rk_get_interfaces', 'static void renamed_get_interfaces').replace('rk->ops->set_to_rmii', 'rk->supports_rmii'))

    def test_patch_cannot_silently_apply_twice(self):
        patched = self.module.prepare(self.original)
        with self.assertRaises(ValueError):
            self.module.prepare(patched)

    @unittest.skipUnless(shutil.which('gcc'), 'C capability regression requires gcc')
    def test_actual_generated_callback_advertises_sgmii_only_for_supported_path(self):
        patched = self.module.prepare(self.original)
        start = patched.index('static void rk_get_interfaces(')
        end = patched.index('static int rk_set_clk_tx_rate', start)
        function = patched[start:end]
        harness = r'''
#include <assert.h>
enum { PHY_INTERFACE_MODE_RMII=7, PHY_INTERFACE_MODE_SGMII=4 };
struct stmmac_priv { int unused; };
struct rk_gmac_ops { void (*set_to_rgmii)(void); void (*set_to_rmii)(void); };
struct rk_priv_data { const struct rk_gmac_ops *ops; int phy_iface; };
static void yes(void) {}
static const struct rk_gmac_ops rk3568_ops = { yes, yes };
static const struct rk_gmac_ops other_ops = { yes, yes };
static void phy_interface_set_rgmii(unsigned long *p) { *p |= 0x1e00; }
static void __set_bit(int n, unsigned long *p) { *p |= 1UL << n; }
''' + function + r'''
int main(void) {
 unsigned long bits=0; struct rk_priv_data rk={&rk3568_ops,PHY_INTERFACE_MODE_SGMII};
 rk_get_interfaces(0,&rk,&bits); assert(bits & (1UL << PHY_INTERFACE_MODE_SGMII));
 assert((bits & 0x1e00) == 0x1e00); assert(bits & (1UL << PHY_INTERFACE_MODE_RMII));
 bits=0; rk.ops=&other_ops; rk_get_interfaces(0,&rk,&bits); assert(!(bits & (1UL << PHY_INTERFACE_MODE_SGMII)));
 bits=0; rk.ops=&rk3568_ops; rk.phy_iface=9; rk_get_interfaces(0,&rk,&bits); assert(!(bits & (1UL << PHY_INTERFACE_MODE_SGMII)));
 return 0;
}
'''
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'caps.c'
            executable = Path(directory) / 'caps'
            source.write_text(harness)
            subprocess.run(['gcc', '-Wall', '-Werror', str(source), '-o', str(executable)], check=True)
            subprocess.run([str(executable)], check=True)

@unittest.skipUnless(shutil.which('dtc') and shutil.which('fdtget'), 'DTB regression requires device-tree-compiler')
class DeviceTreeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        directory = Path(self.temp.name)
        self.path = directory / 'board.dtb'
        source = directory / 'board.dts'
        source.write_text('''/dts-v1/;
/ { compatible = "ariaboard,photonicat", "rockchip,rk3568";
gpio { phandle=<1>; };
usb { marker="preserve-me"; };
ethernet@fe010000 { phy-mode="rgmii-id"; mdio { ethernet-phy@3 { reg=<3>;
 reset-gpios=<1 16 1>; reset-assert-us=<20000>; reset-deassert-us=<100000>; }; }; };
ethernet@fe2a0000 { phy-mode="sgmii"; mdio { ethernet-phy@3 { reg=<3>;
 reset-gpios=<1 22 1>; reset-assert-us=<20000>; reset-deassert-us=<100000>; }; }; };
};''')
        subprocess.run(['dtc', '-q', '-I', 'dts', '-O', 'dtb', '-o', str(self.path), str(source)], check=True)

    def test_move_reset_keeps_addresses_timings_and_usb(self):
        module = load('patch-dtb')
        report = module.patch(self.path, hashlib.sha256(self.path.read_bytes()).hexdigest())
        self.assertEqual(len(report['reset_changes']), 2)
        self.assertEqual(module.command('fdtget', '-ts', str(self.path), '/usb', 'marker'), 'preserve-me')
        for mac in module.MACS:
            self.assertEqual(module.command('fdtget', '-tx', str(self.path), mac, 'snps,reset-delays-us'), '0 4e20 186a0')
            child = mac + '/mdio/ethernet-phy@3'
            self.assertEqual(module.command('fdtget', '-tx', str(self.path), child, 'reg'), '3')
            self.assertNotIn('reset-gpios', module.command('fdtget', '-p', str(self.path), child))

    def test_unknown_dtb_rejected_without_changes(self):
        original = self.path.read_bytes()
        with self.assertRaises(AssertionError):
            load('patch-dtb').patch(self.path, '0'*64)
        self.assertEqual(self.path.read_bytes(), original)

if __name__ == '__main__':
    unittest.main()
