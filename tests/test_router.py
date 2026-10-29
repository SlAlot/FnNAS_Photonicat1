import configparser
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('install_router', ROOT / 'scripts/install-router.py')
INSTALLER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INSTALLER)


class FactoryNetworkTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / 'etc').mkdir()
        (self.root / 'etc/os-release').write_text('ID=debian\n')
        (self.root / 'var/lib/dpkg').mkdir(parents=True)
        (self.root / 'var/lib/dpkg/status').write_text('\n\n'.join(
            f'Package: {name}\nStatus: install ok installed\n' for name in
            ('network-manager', 'dnsmasq-base', 'iptables')))

    def read_profile(self, name):
        parser = configparser.ConfigParser(interpolation=None)
        parser.read(self.root / f'etc/NetworkManager/system-connections/photonicat-{name}.nmconnection')
        return parser

    def test_only_hardware_bound_lan_serves_dhcp(self):
        INSTALLER.install(self.root)
        lan, wan, modem = [self.read_profile(name) for name in ('lan', 'wan', '5g')]
        self.assertEqual(lan['ipv4']['method'], 'shared')
        self.assertEqual(lan['ipv4']['address1'], '192.168.17.1/24')
        self.assertEqual(lan['ipv4']['never-default'], 'true')
        self.assertEqual(lan['connection']['interface-name'], 'pcatlan0')
        self.assertEqual(wan['connection']['interface-name'], 'pcatwan0')
        self.assertEqual(wan['ipv4']['method'], 'auto')
        self.assertEqual(modem['ipv4']['method'], 'auto')
        self.assertLess(int(modem['ipv4']['route-metric']), int(wan['ipv4']['route-metric']))
        self.assertEqual(set(modem['match']['driver'].strip(';').split(';')), {'cdc_ether', 'rndis_host', 'cdc_ncm'})
        self.assertNotIn('interface-name', modem['connection'])
        for filename in ('lan', 'wan', '5g'):
            if os.name != 'nt':
                self.assertEqual((self.root / f'etc/NetworkManager/system-connections/photonicat-{filename}.nmconnection').stat().st_mode & 0o777, 0o600)

    def test_saved_user_configuration_is_never_reset(self):
        INSTALLER.install(self.root)
        path = self.root / 'etc/NetworkManager/system-connections/photonicat-lan.nmconnection'
        path.write_text('[ipv4]\nmethod=manual\naddress1=192.168.27.1/24\n')
        saved = path.read_bytes()
        with self.assertRaises(AssertionError):
            INSTALLER.install(self.root)
        self.assertEqual(path.read_bytes(), saved)

    def test_missing_dependency_stops_image_packaging(self):
        (self.root / 'var/lib/dpkg/status').write_text('')
        with self.assertRaises(AssertionError):
            INSTALLER.install(self.root)
        self.assertFalse((self.root / 'etc/NetworkManager/system-connections').exists())
