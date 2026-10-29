#!/usr/bin/env python3
"""Exercise the image's real NetworkManager in disposable network namespaces."""
import http.client
import ipaddress
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import struct
import subprocess
import sys
import time


def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def output(*args):
    return subprocess.check_output(args, text=True).strip()


def dhcp(interface):
    hook = Path('/tmp/lease-hook.sh')
    hook.write_text("""#!/bin/sh
if [ "$1" = bound ] || [ "$1" = renew ]; then
    printf '%s|%s|%s|%s|%s\\n' "$ip" "$subnet" "$router" "$dns" "$serverid" > /tmp/lan-lease.txt
fi
""")
    hook.chmod(0o755)
    run('/tmp/test-busybox', 'udhcpc', '-i', interface, '-n', '-q', '-f', '-t', '8', '-T', '2', '-s', str(hook),
        stdout=subprocess.DEVNULL)
    address, mask, gateway, dns, server = Path('/tmp/lan-lease.txt').read_text().strip().split('|')
    result = {'address': address, 'server': server, 'gateway': gateway.split()[0], 'dns': dns.split()[0]}
    assert ipaddress.ip_address(address) in ipaddress.ip_network('192.168.17.0/24') and mask == '255.255.255.0', result
    assert result['gateway'] == result['dns'] == result['server'] == '192.168.17.1', result
    run('ip', 'addr', 'add', address + '/24', 'dev', interface)
    run('ip', 'route', 'add', 'default', 'via', result['gateway'])
    print(json.dumps(result))


def syslog():
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
        sock.bind('/dev/log')
        Path('/dev/log').chmod(0o666)
        while True:
            print(sock.recv(16384).decode(errors='replace'), flush=True)


def probe():
    xid = secrets.randbits(16)
    query = struct.pack('!HHHHHH', xid, 0x100, 1, 0, 0, 0) + b'\x0aphotonicat\x04test\0\0\x01\0\x01'
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(4)
        sock.sendto(query, ('192.168.17.1', 53))
        answer = sock.recv(4096)
        assert answer[:2] == struct.pack('!H', xid) and answer[-4:] == socket.inet_aton('10.77.0.1'), answer
    connection = http.client.HTTPConnection('10.77.0.1', 8080, timeout=4)
    connection.request('GET', '/')
    print(connection.getresponse().read().decode())


def inside():
    processes = []
    logs = []

    def spawn(name, *args):
        log = Path('/tmp/' + name + '.log').open('w+')
        logs.append((name, log))
        process = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT)
        processes.append(process)
        return process

    def wait_for(description, check):
        last = None
        for _ in range(45):
            try:
                value = check()
                if value:
                    return value
            except (subprocess.SubprocessError, AssertionError, ValueError) as error:
                last = error
            time.sleep(1)
        raise AssertionError(f'Timed out: {description}; {last}')

    try:
        Path('/etc/machine-id').write_text(secrets.token_hex(16) + '\n')
        Path('/etc/NetworkManager/dnsmasq-shared.d').mkdir(parents=True, exist_ok=True)
        Path('/etc/NetworkManager/dnsmasq-shared.d/test.conf').write_text('log-dhcp\n')
        Path('/run/dbus').mkdir(parents=True, exist_ok=True)
        run('ip', 'link', 'set', 'lo', 'up')
        spawn('syslog', 'python3', '/tmp/router-integration.py', '--syslog')
        wait_for('syslog', lambda: Path('/dev/log').exists())
        spawn('dbus', 'dbus-daemon', '--system', '--nofork', '--nopidfile')
        wait_for('D-Bus', lambda: Path('/run/dbus/system_bus_socket').exists())
        spawn('udev', '/usr/lib/systemd/systemd-udevd', '--resolve-names=never')
        for name, peer, namespace, gateway in (
                ('pcatlan0', 'lanpeer', 'client', None),
                ('pcatwan0', 'wanpeer', 'wired', '192.168.66.1'),
                ('pcat5gtest', 'modempeer', 'mobile', '192.168.100.1')):
            run('ip', 'netns', 'add', namespace)
            run('ip', 'link', 'add', name, 'type', 'veth', 'peer', 'name', peer)
            run('ip', 'link', 'set', peer, 'netns', namespace)
            run('ip', '-n', namespace, 'link', 'set', 'lo', 'up')
            if gateway:
                run('ip', '-n', namespace, 'addr', 'add', gateway + '/24', 'dev', peer)
                run('ip', '-n', namespace, 'addr', 'add', '10.77.0.1/32', 'dev', 'lo')
            else:
                run('ip', '-n', namespace, 'link', 'set', peer, 'up')
        run('udevadm', 'trigger', '--subsystem-match=net')
        run('udevadm', 'settle', '--timeout=10')
        spawn('NetworkManager', 'NetworkManager', '--debug')
        wait_for('NetworkManager', lambda: output('nmcli', '-t', '-f', 'RUNNING', 'general') == 'running')
        drivers = output('nmcli', '-g', 'match.driver', 'connection', 'show', 'Photonicat 5G')
        assert all(name in drivers for name in ('cdc_ether', 'rndis_host', 'cdc_ncm')), drivers
        # The shipped profile is parsed by the image's actual NetworkManager
        # before substituting a veth for the USB hardware in this overlay only.
        run('nmcli', 'connection', 'modify', 'Photonicat 5G', 'match.driver', '',
            'connection.interface-name', 'pcat5gtest')
        for interface in ('pcatlan0', 'pcatwan0', 'pcat5gtest'):
            run('nmcli', 'device', 'set', interface, 'managed', 'yes')
        run('nmcli', '--wait', '15', 'connection', 'up', 'Photonicat LAN')
        run('nmcli', '-f', 'GENERAL,IP4', 'device', 'show', 'pcatlan0')
        run('ss', '-lunp')
        lease = json.loads(output('ip', 'netns', 'exec', 'client', 'python3', '/tmp/router-integration.py', '--dhcp', 'lanpeer'))
        print('PASS: LAN DHCP, gateway and DNS work with both uplinks offline', flush=True)
        for namespace, peer, gateway in (('mobile', 'modempeer', '192.168.100.1'), ('wired', 'wanpeer', '192.168.66.1')):
            run('ip', '-n', namespace, 'link', 'set', peer, 'up')
            prefix = '.'.join(gateway.split('.')[:3])
            spawn(namespace + '-dhcp', 'ip', 'netns', 'exec', namespace, 'dnsmasq', '--no-daemon', '--bind-interfaces',
                  '--interface=' + peer, '--no-resolv', '--dhcp-range=' + prefix + '.50,' + prefix + '.60,5m',
                  '--dhcp-option=3,' + gateway, '--dhcp-option=6,' + gateway, '--address=/photonicat.test/10.77.0.1')
            server = ('import http.server,json; '
                      'handler=type("H",(http.server.BaseHTTPRequestHandler,),'
                      '{"do_GET":lambda s:(s.send_response(200),s.end_headers(),'
                      's.wfile.write(json.dumps({"uplink":' + repr(namespace) + ',"source":s.client_address[0]}).encode()))}); '
                      'http.server.HTTPServer(("10.77.0.1",8080),handler).serve_forever()')
            spawn(namespace + '-http', 'ip', 'netns', 'exec', namespace, 'python3', '-c', server)
        run('nmcli', '--wait', '20', 'connection', 'up', 'Photonicat WAN')
        run('nmcli', '--wait', '20', 'connection', 'up', 'Photonicat 5G')
        # Docker commonly changes this policy to DROP. Sharing must still work.
        run('iptables', '-w', '5', '-P', 'FORWARD', 'DROP')

        def assert_path(expected, prefix):
            value = json.loads(output('ip', 'netns', 'exec', 'client', 'python3', '/tmp/router-integration.py', '--probe'))
            assert value['uplink'] == expected and value['source'].startswith(prefix), value
            return value

        wait_for('5G routing, DNS and NAT', lambda: assert_path('mobile', '192.168.100.'))
        print('PASS: 5G is preferred; DNS and NAT work with FORWARD policy DROP', flush=True)
        run('nmcli', '--wait', '10', 'device', 'disconnect', 'pcat5gtest')
        wait_for('wired fallback', lambda: assert_path('wired', '192.168.66.'))
        print('PASS: wired DHCP uplink takes over after the modem disconnects', flush=True)
        run('nmcli', '--wait', '20', 'connection', 'up', 'Photonicat 5G')
        wait_for('5G recovery', lambda: assert_path('mobile', '192.168.100.'))
        print('PASS: 5G recovers and regains priority', flush=True)
        Path('/tmp/router-result.json').write_text(json.dumps({'status': 'passed', 'lease': lease,
                  'checks': ['offline_lan_dhcp_dns_options', '5g_priority', 'dns_forwarding', 'nat',
                             'docker_forward_drop_policy', 'wired_fallback', '5g_recovery']}))
    finally:
        for command in (('ip', '-br', 'addr'), ('ip', 'route'), ('iptables', '-S'), ('iptables', '-t', 'nat', '-S')):
            subprocess.run(command, check=False)
        for process in reversed(processes):
            process.terminate()
        for process in reversed(processes):
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        for name, log in logs:
            log.seek(0)
            print(f'--- {name} ---\n{log.read()[-14000:]}', flush=True)
            log.close()


def sandbox(root, directory):
    # Called only after unshare has isolated mounts, networking and processes.
    assert os.environ.get('PCAT_NETWORK_SANDBOX') == '1'
    run('mount', '--make-rprivate', '/')
    root, directory = Path(root).resolve(), Path(directory).resolve()
    for name in ('upper', 'overlay-work', 'root'):
        (directory / name).mkdir()
    merged = directory / 'root'
    run('mount', '-t', 'overlay', 'overlay', '-o', f'lowerdir={root},upperdir={directory}/upper,workdir={directory}/overlay-work', str(merged))
    for name in ('run', 'tmp', 'dev'):
        run('mount', '-t', 'tmpfs', 'tmpfs', str(merged / name))
    run('mount', '-t', 'proc', 'proc', str(merged / 'proc'))
    run('mount', '-t', 'sysfs', 'sysfs', str(merged / 'sys'))
    for name, minor in (('null', 3), ('zero', 5), ('random', 8), ('urandom', 9)):
        os.mknod(merged / 'dev' / name, 0o20666, os.makedev(1, minor))
        (merged / 'dev' / name).chmod(0o666)
    (merged / 'dev/fd').symlink_to('/proc/self/fd')
    shutil.copy2(__file__, merged / 'tmp/router-integration.py')
    shutil.copy2(shutil.which('busybox'), merged / 'tmp/test-busybox')
    run('chroot', str(merged), '/usr/bin/env', '-i', 'PATH=/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL=C',
        'python3', '/tmp/router-integration.py', '--inside')
    shutil.copy2(merged / 'tmp/router-result.json', directory / 'result.json')


if __name__ == '__main__':
    if sys.argv[1] == '--dhcp':
        dhcp(sys.argv[2])
    elif sys.argv[1] == '--syslog':
        syslog()
    elif sys.argv[1] == '--probe':
        probe()
    elif sys.argv[1] == '--inside':
        inside()
    elif sys.argv[1] == '--sandbox':
        sandbox(*sys.argv[2:])
    else:
        raise SystemExit('Run in the build-only namespace sandbox')
