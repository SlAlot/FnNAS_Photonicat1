import importlib.util
import json
from pathlib import Path
import runpy
import socket
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
CTL = runpy.run_path(str(ROOT / 'files/usr/local/sbin/photonicat-hotspotctl'))


class HotspotPolicyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.profiles = Path(temporary.name)
        self.card = {'id': 'board', 'interface': 'wlan0', 'mac': '02:00:00:00:00:10',
                     'supports_ap': True, 'active': False, 'channels': {'bg': [1, 6, 11], 'a': []}}
        self.globals = CTL['apply'].__globals__
        self.context = patch.dict(self.globals, {'PROFILES': self.profiles, 'devices': lambda: [self.card]})
        self.context.start()
        self.addCleanup(self.context.stop)
        self.request = {'id': 'board', 'enabled': True, 'ssid': 'Test hotspot', 'password': 'test-passphrase',
                        'band': 'bg', 'channel': 6}

    def test_lan_cannot_be_selected_as_a_hotspot(self):
        with self.assertRaises(ValueError):
            CTL['apply']({**self.request, 'id': 'pcatlan0'})
        self.assertEqual(list(self.profiles.iterdir()), [])

    def test_keyfile_injection_and_unavailable_ap_channels_are_rejected(self):
        for change in ({'ssid': 'bad\n[ipv4]'}, {'band': 'a', 'channel': 36}, {'enabled': 'true'}):
            with self.assertRaises(ValueError):
                CTL['apply']({**self.request, **change})
        self.assertEqual(list(self.profiles.iterdir()), [])

    def test_activation_failure_restores_previous_profile(self):
        original = b'[connection]\nautoconnect=false\n[wifi]\nssid=old\n[wifi-security]\npsk=old-password\n'
        target = self.profiles / 'photonicat-ap-board.nmconnection'
        target.write_bytes(original)
        def command(*arguments, **kwargs):
            if arguments[:3] == ('ip', '-j', '-4'):
                return type('Result', (), {'stdout': '[{"ifname":"pcatlan0","addr_info":[{"local":"192.168.17.1","prefixlen":24}]}]'})()
            if arguments[:3] == ('nmcli', '--wait', '45'):
                raise ValueError('simulated firmware start failure')
            return type('Result', (), {'stdout': '', 'returncode': 0})()
        with patch.dict(self.globals, {'run': command}):
            with self.assertRaises(ValueError):
                CTL['apply'](self.request)
        self.assertEqual(target.read_bytes(), original)

    def test_automatic_channel_uses_native_selection_without_opening_forbidden_band(self):
        def command(*arguments, **kwargs):
            return type('Result', (), {'stdout': '[]' if arguments[:3] == ('ip', '-j', '-4') else '',
                                       'returncode': 0})()
        with patch.dict(self.globals, {'run': command}):
            CTL['apply']({**self.request, 'channel': 0})
            saved = (self.profiles / 'photonicat-ap-board.nmconnection').read_text()
            self.assertIn('channel=1\n', saved)
            self.assertIn('photonicat.channel=auto', saved)
            with self.assertRaises(ValueError):
                CTL['apply']({**self.request, 'band': 'a', 'channel': 0})

    def test_auto_channel_avoids_busy_spectrum_and_forbidden_channels(self):
        scan = 'BSS test\n\tfreq: 2412\n\tsignal: -30.0 dBm\nBSS other\n\tfreq: 2437\n\tsignal: -40.0 dBm\n'
        self.assertEqual(CTL['choose_channel']([1, 6, 11], 'bg', scan), 11)
        self.assertEqual(CTL['choose_channel']([6], 'bg', ''), 6)

    def test_width_rejects_forbidden_or_incomplete_channel_blocks(self):
        plan = CTL['channel_plan']
        self.assertEqual(plan([36, 40, 44, 48], 'a', 36, 80), (1, 5210))
        self.assertEqual(plan([36, 40, 44, 48], 'a', 40, 80), (-1, 5210))
        self.assertEqual(plan([149, 153, 157, 161], 'a', 161, 80), (-1, 5775))
        self.assertEqual(plan(list(range(1, 12)), 'bg', 11, 40), (-1, 2452))
        for allowed, band, channel, width in (([36, 40, 44], 'a', 36, 80),
            ([36, 40, 44, 48], 'a', 36, 160), ([1, 6, 11], 'bg', 1, 40)):
            with self.assertRaises(ValueError):
                plan(allowed, band, channel, width)

    def test_phy_filters_no_ir_before_exposing_widths(self):
        phy = '''Wiphy phy0
        Band 1:
          HT20/HT40
          * 2412 MHz [1] (20.0 dBm)
          * 2432 MHz [5] (20.0 dBm)
        Band 2:
          HT20/HT40
          VHT Capabilities (0x1234):
          Supported Channel Width: neither 160 nor 80+80
          * 5180 MHz [36] (20.0 dBm)
          * 5200 MHz [40] (20.0 dBm)
          * 5220 MHz [44] (20.0 dBm)
          * 5240 MHz [48] (20.0 dBm) (no IR)
        '''
        channels, widths, blocked = CTL['phy_capabilities'](phy)
        self.assertEqual(channels['a'], [36, 40, 44])
        self.assertEqual(widths['bg']['1'], [20, 40])
        self.assertEqual(widths['a']['36'], [20, 40])
        self.assertTrue(blocked['a'])

    def test_width_does_not_modify_other_connection_roles(self):
        self.assertIsNone(CTL['restore_width']('pcatlan0', 'other-uuid'))

    def test_width_and_channel_must_be_compatible(self):
        self.card['channel_widths'] = {'bg': {'1': [20], '6': [20], '11': [20]}, 'a': {}}
        for width in (40, 80, 160, '80', True):
            with self.assertRaises(ValueError):
                CTL['apply']({**self.request, 'bandwidth': width})


@unittest.skipUnless(hasattr(socket, 'AF_UNIX') and hasattr(__import__('socketserver'), 'UnixStreamServer'),
                     'Unix socket gateway regression requires Linux')
class HotspotAuthTests(unittest.TestCase):
    def test_forged_gateway_headers_cannot_authorize_a_request(self):
        spec = importlib.util.spec_from_file_location('hotspot_server', ROOT / 'apps/photonicat-hotspot/app/server.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            address = str(Path(directory) / 'test.sock')
            with module.Server(address, module.Handler) as server:
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                        client.connect(address)
                        client.sendall(b'GET /photonicat-hotspot/api HTTP/1.0\r\nHost: localhost\r\nX-Trim-Isadmin: true\r\nX-Trim-Userid: 1000\r\n\r\n')
                        response = b''
                        while data := client.recv(8192):
                            response += data
                    self.assertIn(b'401 Unauthorized', response)
                finally:
                    server.shutdown()
                    thread.join()
