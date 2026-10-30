"""Reversible, kernel-matched ath11k extension; no files in /lib are replaced."""
import hashlib
import json
import os
import platform
from pathlib import Path
import re
import subprocess
import time

BUILD = '0.2.0-reg905'
MARKER = Path('/sys/module/ath11k/parameters/photonicat_plugin_build')


def execute(*args, check=True):
    result = subprocess.run(args, capture_output=True, text=True, timeout=30)
    if check and result.returncode:
        raise ValueError(result.stderr.strip()[:250] or '无线增强驱动操作失败')
    return result


def save(path, value):
    tmp = path.with_suffix('.tmp')
    fd = os.open(tmp, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'w') as stream:
        stream.write(json.dumps(value))
    os.replace(tmp, path)


class Extension:
    def __init__(self, app, state):
        self.app, self.state = Path(app), Path(state)
        self.record = self.state/'driver-owner.json'
        self.notice = ''

    def owns_module(self):
        return MARKER.exists() and MARKER.read_text().strip() == BUILD

    def interfaces(self, pci):
        return sorted(p.name for p in (Path('/sys/bus/pci/devices')/pci/'net').glob('*'))

    def wait_interface(self, pci):
        for _ in range(100):
            names = self.interfaces(pci)
            if len(names) == 1:
                return names[0]
            time.sleep(.2)
        raise ValueError('外接无线网卡未完成初始化')

    def compatible(self):
        directory = self.app/'drivers'/platform.release()
        manifest = directory/'build.json'
        if not manifest.is_file():
            return None
        report = json.loads(manifest.read_text())
        if report.get('marker') != BUILD or report.get('kernel_release') != platform.release():
            raise ValueError('无线增强模块与当前内核不匹配')
        for name in ('cfg80211', 'mac80211'):
            path = Path(execute('modinfo', '-n', name).stdout.strip())
            if hashlib.sha256(path.read_bytes()).hexdigest() != report['stack_sha256'][name]:
                raise ValueError('系统无线栈已更新，暂使用系统驱动')
        for name in ('ath11k', 'ath11k_pci'):
            path = directory/(name+'.ko')
            if hashlib.sha256(path.read_bytes()).hexdigest() != report['sha256'][name]:
                raise ValueError('无线增强模块校验失败')
            magic = execute('modinfo', '-F', 'vermagic', str(path)).stdout.strip()
            if magic != report['vermagic']:
                raise ValueError('无线增强模块 ABI 校验失败')
        return directory

    def prepare(self):
        directory = self.compatible()
        if directory is None:
            self.notice = '此内核未附带增强模块，使用系统驱动。'
            return
        cards = list(Path('/sys/bus/pci/drivers/ath11k_pci').glob('????:??:??.?'))
        if len(cards) != 1:
            self.notice = '使用系统驱动；增强仅适用于单张已适配的 QCNFA765。'
            return
        pci = cards[0].name
        device = Path('/sys/bus/pci/devices')/pci
        if (device/'vendor').read_text().strip() != '0x17cb' or (device/'device').read_text().strip() != '0x1103':
            return
        names = self.interfaces(pci)
        if len(names) != 1:
            raise ValueError('无线网卡存在其他接口，未接管增强驱动')
        interface = names[0]
        current = execute('nmcli', '-g', 'GENERAL.CON-UUID', 'device', 'show', interface).stdout.strip()
        link = json.loads(execute('ip', '-j', 'addr', 'show', 'dev', interface).stdout)[0]
        mode = execute('/usr/sbin/iw', 'dev', interface, 'info').stdout
        if current not in ('', '--') or any(x.get('scope') == 'global' for x in link.get('addr_info', [])) or not re.search(r'^\s*type managed$', mode, re.M):
            self.notice = '外接网卡正在使用其他连接，保留系统驱动。'
            return
        rule = Path('/run/udev/rules.d/90-photonicat-ap-driver.rules')
        body = 'SUBSYSTEM=="net", KERNELS=="'+pci+'", ENV{NM_UNMANAGED}="1"\n'
        if rule.exists():
            raise ValueError('无线增强规则已有其他所有者')
        owner = dict(pci=pci, interface=interface, mac=link.get('permaddr', link['address']),
                     managed=execute('nmcli', '-g', 'GENERAL.NM-MANAGED', 'device', 'show', interface).stdout.strip().lower() in ('yes','true','是'),
                     rule=body)
        save(self.record, owner)
        try:
            rule.parent.mkdir(parents=True, exist_ok=True)
            rule.write_text(body)
            execute('udevadm', 'control', '--reload-rules')
            execute('nmcli', 'device', 'set', interface, 'managed', 'no')
            time.sleep(1)
            execute('rmmod', 'ath11k_pci')
            execute('rmmod', 'ath11k')
            execute('insmod', str(directory/'ath11k.ko'))
            execute('insmod', str(directory/'ath11k_pci.ko'))
            interface = self.wait_interface(pci)
            execute('nmcli', 'device', 'set', interface, 'managed', 'no')
            if not self.owns_module():
                raise ValueError('无线增强模块身份校验失败')
            self.notice = '外接无线增强已启用。'
        except Exception:
            self.restore()
            raise

    def restore(self):
        if not self.record.exists():
            if self.owns_module():
                raise ValueError('增强驱动缺少归属记录，未自动卸载')
            return
        owner = json.loads(self.record.read_text())
        pci = owner['pci']
        if not re.fullmatch(r'[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]', pci):
            raise ValueError('无线驱动恢复记录无效')
        if self.owns_module():
            for interface in self.interfaces(pci):
                current = execute('nmcli','-g','GENERAL.CON-UUID','device','show',interface,check=False).stdout.strip()
                if current not in ('','--'):
                    raise ValueError('增强网卡已被其他连接使用，无法自动卸载')
            if Path('/sys/module/ath11k_pci').exists():execute('rmmod','ath11k_pci')
            execute('rmmod','ath11k')
        rule = Path('/run/udev/rules.d/90-photonicat-ap-driver.rules')
        if rule.exists():
            if rule.read_text() != owner['rule']:
                raise ValueError('无线规则归属变化，未删除其他程序的规则')
            rule.unlink()
            execute('udevadm','control','--reload-rules')
        execute('modprobe','ath11k_pci')
        if not (Path('/sys/bus/pci/devices')/pci).exists():
            self.record.unlink()
            return
        interface = self.wait_interface(pci)
        # Refresh the NM_UNMANAGED udev property on an interface surviving a crash.
        execute('udevadm','trigger','--action=change','/sys/class/net/'+interface)
        execute('udevadm','settle')
        link = json.loads(execute('ip','-j','link','show','dev',interface).stdout)[0]
        if link.get('permaddr',link['address']).lower() == owner['mac'].lower():
            execute('nmcli','device','set',interface,'managed','yes' if owner['managed'] else 'no')
        self.record.unlink()
