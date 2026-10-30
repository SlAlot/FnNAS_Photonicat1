import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

APP=Path(__file__).resolve().parents[1]/'apps/photonicat-hotspot/app'
sys.path.insert(0,str(APP))
from radio import capabilities,plan
from ap import Runtime,hostapd_config
from driver import Extension


class ChannelTests(unittest.TestCase):
    def fixture(self,radar=True):
        return '''Wiphy phy1
          Supported interface modes:
            * AP
          Band 2:
            HT20/HT40
            VHT Capabilities (0x123):
              Supported Channel Width: 160 MHz
            HE Iftypes: AP
              HE40/HE80/5GHz
              HE160/5GHz
            Frequencies:
              * 5180 MHz [36] (20.0 dBm) (no IR)
              * 5260 MHz [52] (20.0 dBm) (no IR, radar detection)
              * 5280 MHz [56] (20.0 dBm) (no IR, radar detection)
              * 5300 MHz [60] (20.0 dBm) (no IR, radar detection)
              * 5320 MHz [64] (20.0 dBm) (no IR, radar detection)
        '''+('radar detect widths: { 20 MHz, 40 MHz, 80 MHz, 160 MHz }' if radar else '')

    def test_dfs_is_exposed_only_with_real_radar_support(self):
        cap=capabilities(self.fixture())
        self.assertNotIn(36,cap['channels']['a'])
        self.assertEqual(cap['channels']['a'],[52,56,60,64])
        self.assertEqual(cap['channel_widths']['a']['52'],[20,40,80])
        self.assertEqual(capabilities(self.fixture(False))['channels']['a'],[])

    def test_160_requires_every_constituent_channel(self):
        with self.assertRaises(ValueError):plan([36,40,44,48],'a',36,160)
        self.assertEqual(plan(list(range(36,68,4)),'a',36,160),(1,5250))

    def test_hostapd_uses_hex_ssid_and_derived_key(self):
        card=dict(capabilities(self.fixture()),interface='wlan0')
        value={'band':'a','actual_channel':52,'bandwidth':80,'ssid':'测试 AP','password':'secret-password'}
        cfg=hostapd_config(card,value,'pcat-ap-e',Path('/private/control'))
        self.assertIn('ieee80211h=1\n',cfg)
        self.assertIn('ieee80211ax=1\n',cfg)
        self.assertIn('vht_oper_centr_freq_seg0_idx=58\n',cfg)
        self.assertIn('ssid2='+value['ssid'].encode().hex(),cfg)
        self.assertNotIn(value['password'],cfg)

    def test_160_is_advertised_and_short_gi_matches_hardware(self):
        info = self.fixture().replace('0x123','0x3391f9f6').replace('(no IR)','').replace('              * 5260',
            '              * 5200 MHz [40] (20.0 dBm)\n              * 5220 MHz [44] (20.0 dBm)\n              * 5240 MHz [48] (20.0 dBm)\n              * 5260')
        card=dict(capabilities(info),interface='wlan0')
        value={'band':'a','actual_channel':36,'bandwidth':160,'ssid':'test','password':'test-password'}
        cfg=hostapd_config(card,value,'pcat-ap-e',Path('/private/control'))
        self.assertIn('[VHT160]',cfg)
        self.assertIn('[SHORT-GI-160]',cfg)
        self.assertIn('[RXLDPC]',cfg)
        self.assertIn('[MAX-A-MPDU-LEN-EXP7]',cfg)
        value['bandwidth']=80
        self.assertNotIn('[VHT160]',hostapd_config(card,value,'pcat-ap-e',Path('/private/control')))


class DriverBoundaryTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        self.directory=Path(folder.name)
        self.extension=Extension(self.directory,self.directory)

    def test_unknown_kernel_does_not_load_or_unload_drivers(self):
        with patch('driver.execute',side_effect=AssertionError('must not modify drivers')):
            self.extension.prepare()
        self.assertIn('系统驱动',self.extension.notice)

    def test_foreign_connection_blocks_module_restore(self):
        self.extension.record.write_text(json.dumps({'pci':'0002:21:00.0'}))
        with patch.object(self.extension,'owns_module',return_value=True),patch.object(self.extension,'interfaces',return_value=['wlan0']),patch('driver.execute',return_value=type('Result',(),{'stdout':'foreign-uuid'})()) as execute:
            with self.assertRaises(ValueError):self.extension.restore()
        self.assertFalse(any(c.args[0] in ('rmmod','insmod','modprobe') for c in execute.call_args_list))
        self.assertTrue(self.extension.record.exists())


class OwnershipTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        self.runtime=Runtime(Path(folder.name)/'app',Path(folder.name)/'data')
        self.card={'id':'board','interface':'wlan1','mac':'02:00:00:00:00:01','managed':True}

    def test_factory_defaults_do_not_reset_saved_or_disabled_hotspots(self):
        import shutil
        self.runtime.app.mkdir()
        shutil.copy2(APP/'defaults.json',self.runtime.app/'defaults.json')
        self.runtime.saved['board']={'enabled':False,'ssid':'user-kept-name'}
        self.runtime.seed_defaults([self.card,dict(self.card,id='external')])
        self.assertEqual(self.runtime.saved['board'],{'enabled':False,'ssid':'user-kept-name'})
        external=self.runtime.saved['external']
        self.assertEqual((external['ssid'],external['band'],external['bandwidth'],external['channel']),('SlAlot-GLM','a',80,0))
        self.assertTrue(external['enabled'])

    def test_cannot_control_lan_as_a_wireless_role(self):
        with patch.object(self.runtime,'discover',return_value=[self.card]),patch('ap.run',side_effect=AssertionError('must not modify networking')):
            with self.assertRaises(ValueError):self.runtime.apply({'id':'pcatlan0','enabled':True})

    def test_does_not_disconnect_an_existing_foreign_connection(self):
        def execute(*args,**kwargs):
            value='yes' if 'GENERAL.NM-MANAGED' in args else 'foreign-connection'
            return type('Result',(),{'stdout':value,'returncode':0})()
        with patch('ap.run',side_effect=execute) as call:
            with self.assertRaises(ValueError):self.runtime.snapshot(self.card)
            self.assertTrue(all('set' not in c.args and 'down' not in c.args for c in call.call_args_list))

    def test_does_not_change_country_under_another_wireless_connection(self):
        with patch.object(self.runtime,'discover',return_value=[self.card]),patch.object(self.runtime,'country',return_value='US'),patch.object(self.runtime,'foreign_wireless',return_value=['wlan0']),patch('ap.run',side_effect=AssertionError('must preserve existing wireless connection')):
            with self.assertRaises(ValueError):self.runtime.apply({'id':'board','enabled':True})

    def test_does_not_restore_management_to_a_different_physical_card(self):
        self.runtime.owners['board']=self.card
        calls=[]
        def execute(*args,**kwargs):
            calls.append(args)
            out='[{"address":"02:00:00:00:00:99"}]' if args[:3]==('ip','-j','link') else ''
            return type('Result',(),{'stdout':out,'returncode':0})()
        with patch('ap.run',side_effect=execute):self.runtime.stop('board')
        self.assertFalse(any(x[:3]==('nmcli','device','set') or x[:3]==('ip','link','set') for x in calls))
        self.assertTrue(all('pcatlan0' not in x and 'usb0' not in x for x in calls))


if __name__=='__main__':unittest.main()
