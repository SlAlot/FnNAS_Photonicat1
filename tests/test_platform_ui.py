import importlib.util
import json
from pathlib import Path
import runpy
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('platform_ui', ROOT / 'scripts/patch-network-editor.py')
UI = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(UI)


class HardwareTests(unittest.TestCase):
    def test_gpu_is_reported_only_with_real_driver_and_kernel_identity(self):
        detect = runpy.run_path(str(ROOT / 'files/usr/local/sbin/photonicat-hardware'))['detect_gpu']
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            device = root / 'sys/devices/platform/fde60000.gpu'
            (device / 'of_node').mkdir(parents=True)
            (device / 'of_node/compatible').write_bytes(b'rockchip,rk3568-mali\0arm,mali-bifrost\0')
            (device / 'gpuinfo').write_text('Mali-G52 1 cores r1p0 0x7402\n')
            self.assertEqual(detect(root), [])
            (device / 'driver').mkdir()
            self.assertEqual(detect(root)[0]['device'], 'Mali-G52')
            (device / 'gpuinfo').write_text('unknown\n')
            self.assertEqual(detect(root), [])


@unittest.skipUnless(shutil.which('node'), 'JavaScript contract tests require Node.js')
class EditorContracts(unittest.TestCase):
    def test_lan_settings_and_missing_gpu_fallback_preserve_native_data(self):
        javascript = UI.HELPER + '''
const assert = (await import('node:assert/strict')).default;
const lan = {ifName:'pcatlan0',ipv4Dhcp:true,defaultIpv4Gateway:true,ipv4Gateway:'1.2.3.4',ipv4Addr:'192.168.27.1',ipv4Dns:['223.5.5.5'],mtu:1492};
const saved = pcatLanSettings(lan);
assert.equal(saved.ipv4Dhcp,false);assert.equal(saved.defaultIpv4Gateway,false);assert.equal(saved.ipv4Gateway,'');
assert.equal(saved.ipv4Addr,lan.ipv4Addr);assert.deepEqual(saved.ipv4Dns,lan.ipv4Dns);assert.equal(saved.mtu,1492);
assert.equal(lan.ipv4Dhcp,true);
const wan = {...lan,ifName:'pcatwan0'};assert.equal(pcatLanSettings(wan),wan);
assert.deepEqual(pcatBondCandidates([{name:'pcatlan0'},{name:'pcatwan0'},{name:'usb0'}]),[{name:'pcatwan0'},{name:'usb0'}]);
const native = {cpu:{name:'RK3568'},mem:{total:4000},gpu:[]};
globalThis.fetch=async()=>({ok:true,json:async()=>({gpu:[{vendor:'Arm',device:'Mali-G52'}]})});
const info = await pcatHardwareInfo(native);assert.equal(info.gpu[0].device,'Mali-G52');assert.equal(info.cpu,native.cpu);assert.equal(info.mem,native.mem);
globalThis.fetch=async()=>{throw Error('unavailable')};assert.equal(await pcatHardwareInfo(native),native);
assert.equal(await pcatHardwareInfo(info),info);
console.log('LAN save and hardware fallback contracts passed');
'''
        subprocess.run(['node', '--input-type=module', '-e', javascript], check=True, capture_output=True)

    def test_unknown_source_context_is_rejected(self):
        with self.assertRaises(ValueError):
            UI.adapt('unrecognized upstream editor')
