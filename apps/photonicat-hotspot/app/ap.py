"""Private hostapd runtime. Own bridges only; official network services stay running."""
import configparser
import hashlib
import ipaddress
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import socket
import subprocess
import threading
import time
import uuid

from radio import ROLES, capabilities, choose, plan
from driver import Extension


def run(*args, check=True, timeout=35):
    result = subprocess.run(args, text=True, capture_output=True, timeout=timeout)
    if check and result.returncode:
        raise ValueError(result.stderr.strip()[:350] or '无线网络操作失败')
    return result


def atomic(path, contents):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as stream:
        stream.write(contents)
    os.replace(temporary, path)


def hostapd_config(card, value, bridge, control):
    band, channel = value['band'], value['actual_channel']
    width=value.get('effective_width',value['bandwidth'] or 20)
    offset, center = plan(card['channels'][band], band, channel, width)
    modes = card['modes'][band]
    lines = ['driver=nl80211', 'interface='+card['interface'], 'bridge='+bridge,
             'ctrl_interface='+str(control), 'country_code=CN', 'ieee80211d=1',
             'hw_mode='+('g' if band == 'bg' else 'a'), 'channel='+str(channel),
             'ssid2='+value['ssid'].encode().hex(), 'wmm_enabled=1', 'auth_algs=1',
             'wpa=2', 'wpa_key_mgmt=WPA-PSK', 'rsn_pairwise=CCMP',
             'wpa_psk='+hashlib.pbkdf2_hmac('sha1', value['password'].encode(), value['ssid'].encode(), 4096, 32).hex(),
             'logger_stdout=-1', 'logger_stdout_level=2']
    if band == 'a':
        lines.append('ieee80211h=1')
    if modes['ht']:
        lines.append('ieee80211n=1')
        flags = [flag for flag in modes.get('ht_flags',[]) if width>=40 or flag!='SHORT-GI-40']
        if offset:
            flags.append('HT40'+('+' if offset > 0 else '-'))
        if flags:lines.append('ht_capab='+''.join('['+flag+']' for flag in flags))
    center_channel = (center-5000)//5 if band == 'a' else 0
    oper_width = 2 if width == 160 else 1 if width == 80 else 0
    if modes['vht'] and band == 'a':
        lines.extend(['ieee80211ac=1', 'vht_oper_chwidth='+str(oper_width)])
        flags = [flag for flag in modes.get('vht_flags',[]) if width==160 or flag not in ('VHT160','SHORT-GI-160')]
        if flags:lines.append('vht_capab='+''.join('['+flag+']' for flag in flags))
        if width >= 80:
            lines.append('vht_oper_centr_freq_seg0_idx='+str(center_channel))
    if modes['he']:
        lines.extend(['ieee80211ax=1', 'he_oper_chwidth='+str(oper_width)])
        if band == 'a' and width >= 80:
            lines.append('he_oper_centr_freq_seg0_idx='+str(center_channel))
    return '\n'.join(lines)+'\n'


class Runtime:
    def __init__(self, app, data):
        self.app, self.data = Path(app), Path(data)
        self.state = self.data/'runtime'
        self.state.mkdir(parents=True, exist_ok=True)
        self.settings = self.data/'hotspots.json'
        self.saved = json.loads(self.settings.read_text()) if self.settings.exists() else {}
        self.owners, self.processes, self.errors, self.progress = {}, {}, {}, {}
        self.lock = threading.RLock()
        self.cache = (0, [])
        self.country_file=self.state/'country-before'
        self.extension = Extension(self.app, self.state)
        self.recover()

    def country(self):
        match=re.search(r'^global\ncountry ([A-Z0-9]{2}):',run('/usr/sbin/iw','reg','get',check=False).stdout,re.M)
        return match[1] if match else None

    def foreign_wireless(self):
        owned = {card['interface'] for card in self.owners.values()}
        status = run('nmcli','-t','-f','DEVICE,TYPE,STATE','device','status').stdout
        return [fields[0] for line in status.splitlines() if len(fields:=line.split(':'))==3
                and fields[1]=='wifi' and fields[2].startswith(('connected','connecting')) and fields[0] not in owned]

    def discover(self, fresh=False):
        if not fresh and time.monotonic()-self.cache[0] < 15:
            return self.cache[1]
        rows = []
        for phy in sorted(Path('/sys/class/ieee80211').glob('*')):
            driver = (phy/'device/driver').resolve().name
            if driver not in ROLES:
                continue
            nets = list((phy/'device/net').glob('*'))
            if len(nets) != 1 or not re.fullmatch(r'[A-Za-z0-9_.-]{1,15}', nets[0].name):
                continue
            interface = nets[0].name
            link = json.loads(run('ip', '-j', 'link', 'show', 'dev', interface).stdout)[0]
            mac = link.get('permaddr', link['address'])
            if not re.fullmatch(r'(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}', mac):
                continue
            role, title, subnet = ROLES[driver]
            cap = capabilities(run('/usr/sbin/iw', 'phy', phy.name, 'info').stdout)
            rows.append(dict(cap, id=role, title=title, interface=interface, mac=mac, subnet=subnet,
                             gateway=str(ipaddress.ip_interface(subnet).ip)))
        self.cache = (time.monotonic(), rows)
        return rows

    def identity(self, role):
        if role not in ('board', 'external'):
            raise ValueError('无线网卡无效')
        return str(uuid.uuid5(uuid.NAMESPACE_DNS, 'photonicat.plugin.bridge.'+role))

    def control(self, role, command):
        card = self.owners.get(role)
        if not card:
            return {}
        local = self.state/('query-'+role+'.sock')
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            local.unlink(missing_ok=True)
            sock.bind(str(local));sock.settimeout(1)
            try:
                sock.connect(str(self.state/('ctrl-'+role)/card['interface']))
                sock.send(command.encode())
                response = sock.recv(16384).decode(errors='replace')
                return dict(line.split('=', 1) for line in response.splitlines() if '=' in line)
            except OSError:
                return {}
            finally:
                local.unlink(missing_ok=True)

    def status(self):
        with self.lock:
            rows = []
            for card in self.discover():
                role = card['id'];value = self.saved.get(role, {})
                report = self.control(role, 'STATUS')
                mode = run('/usr/sbin/iw', 'dev', card['interface'], 'info').stdout
                channel = re.search(r'channel (\d+) \(', mode)
                width = re.search(r'width: (\d+) MHz', mode)
                state = report.get('state', self.progress.get(role, 'DISABLED'))
                process = self.processes.get(role)
                if process and process.poll() is not None:
                    state = 'ERROR';self.errors.setdefault(role, '热点进程退出，请检查频段和频宽后重新应用')
                public = {k:v for k,v in card.items() if k != 'subnet'}
                public.update(ssid=value.get('ssid', ''), password_set=bool(value.get('password')),
                              band=value.get('band', 'a' if card['channels']['a'] else 'bg'), channel=value.get('channel', 0),
                              bandwidth=value.get('bandwidth', 0), enabled=value.get('enabled', False),
                              active=state=='ENABLED', ap_state=state, error=self.errors.get(role, ''),
                              actual_channel=int(channel[1]) if role in self.owners and channel else None,
                              actual_bandwidth=int(width[1]) if role in self.owners and width else None)
                public['standard']='Wi-Fi 6' if report.get('ieee80211ax')=='1' else 'Wi-Fi 5' if report.get('ieee80211ac')=='1' else 'Wi-Fi 4' if report.get('ieee80211n')=='1' else ''
                public['driver_note'] = self.extension.notice if role == 'external' else ''
                rows.append(public)
            return rows

    def snapshot(self, card):
        managed = run('nmcli', '-g', 'GENERAL.NM-MANAGED', 'device', 'show', card['interface']).stdout.strip()
        active = run('nmcli', '-g', 'GENERAL.CON-UUID', 'device', 'show', card['interface']).stdout.strip()
        if active and active != '--':
            raise ValueError('此网卡正在使用其他连接，请先断开该无线连接再开启热点')
        addresses=json.loads(run('ip','-j','addr','show','dev',card['interface']).stdout)
        if any(addr.get('scope')=='global' for item in addresses for addr in item.get('addr_info',[])):
            raise ValueError('此网卡已有网络地址，请先停止其原有连接再开启热点')
        return dict(card, managed=managed.lower() in ('yes', 'true', '是'))

    def start(self, card, value):
        role = card['id'];self.progress[role] = 'STARTING'
        owner = self.snapshot(card)
        bridge = 'pcat-ap-'+('b' if role == 'board' else 'e')
        if run('ip','link','show','dev',bridge,check=False).returncode==0:
            raise ValueError('热点网桥名称已被其他连接使用')
        if not self.owners and not self.country_file.exists():
            country=self.country()
            if country:atomic(self.country_file,country)
        self.owners[role] = owner
        atomic(self.state/('owner-'+role+'.json'), json.dumps(owner))
        try:
            run('nmcli', 'device', 'set', card['interface'], 'managed', 'no')
            time.sleep(1)
            if value['channel']==0:
                band,width = value['band'],value['bandwidth']
                allowed = [c for c in card['channels'][band] if width in (0,20) or width in card['channel_widths'][band].get(str(c),[])]
                if width==0 and band=='a':
                    wide = [c for c in allowed if 80 in card['channel_widths'][band].get(str(c),[])]
                    if wide:allowed=wide
                immediate = [c for c in allowed if c not in card['dfs_channels']]
                if immediate:allowed=immediate
                run('ip','link','set','dev',card['interface'],'up')
                scan = run('/usr/sbin/iw','dev',card['interface'],'scan',check=False)
                if scan.returncode:scan=run('/usr/sbin/iw','dev',card['interface'],'scan','dump',check=False)
                value['actual_channel'] = choose(allowed,band,scan.stdout)
            value['effective_width'] = value['bandwidth'] or (80 if value['band']=='a' and 80 in card['channel_widths']['a'].get(str(value['actual_channel']),[]) else 20)
            run('ip', 'link', 'set', 'dev', card['interface'], 'down')
            profile = Path('/run/NetworkManager/system-connections')/('photonicat-plugin-'+role+'.nmconnection')
            body = ('[connection]\nid=Photonicat plugin AP '+role+'\nuuid='+self.identity(role)+
                    '\ntype=bridge\ninterface-name='+bridge+'\nautoconnect=false\n\n[bridge]\nstp=false\n\n'
                    '[ipv4]\nmethod=shared\naddress1='+card['subnet']+'\nnever-default=true\n\n[ipv6]\nmethod=disabled\n')
            atomic(profile, body)
            run('nmcli', 'connection', 'load', str(profile))
            run('nmcli', '--wait', '20', 'connection', 'up', 'uuid', self.identity(role))
            control = self.state/('ctrl-'+role);control.mkdir(mode=0o700, exist_ok=True)
            cfg = self.state/('hostapd-'+role+'.conf')
            atomic(cfg, hostapd_config(card, value, bridge, control))
            log = self.state/('hostapd-'+role+'.log')
            proc = subprocess.Popen([str(self.app/'bin/hostapd'), str(cfg)],stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
            self.processes[role] = proc
            def capture():
                logger=logging.Logger('hostapd-'+role)
                handler=RotatingFileHandler(log,maxBytes=512*1024,backupCount=1,encoding='utf-8')
                logger.addHandler(handler)
                try:
                    for line in iter(proc.stdout.readline,b''):logger.warning(line.decode(errors='replace').rstrip())
                finally:handler.close();proc.stdout.close()
            threading.Thread(target=capture,daemon=True).start()
            atomic(self.state/('pid-'+role), str(proc.pid))
            for _ in range(100):
                if proc.poll() is not None:
                    raise ValueError('热点启动失败：'+self.failure(log))
                state = self.control(role, 'STATUS').get('state', '')
                if state == 'ENABLED':
                    self.progress[role] = 'ENABLED';return
                if state == 'DFS':
                    self.progress[role] = 'DFS';return
                time.sleep(.2)
            raise ValueError('热点启动超时，请检查驱动允许的频段')
        except Exception:
            self.stop(role)
            raise

    def failure(self, log):
        if not log.exists():return '驱动未接受所选设置'
        lines = log.read_text(errors='replace').splitlines()[-20:]
        good = [x for x in lines if any(word in x.lower() for word in ('failed', 'not supported', 'not allowed', 'invalid', 'could not'))]
        return '；'.join(good[-3:])[:350] or '驱动未接受所选设置'

    def stop(self, role):
        owner = self.owners.get(role)
        if not owner:
            return
        proc = self.processes.pop(role, None)
        if proc and proc.poll() is None:
            proc.terminate()
            try:proc.wait(timeout=8)
            except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=3)
        run('nmcli', '--wait', '20', 'connection', 'down', 'uuid', self.identity(role), check=False)
        run('nmcli', 'connection', 'delete', 'uuid', self.identity(role), check=False)
        profile = Path('/run/NetworkManager/system-connections')/('photonicat-plugin-'+role+'.nmconnection')
        profile.unlink(missing_ok=True)
        interface = owner['interface']
        link=run('ip','-j','link','show','dev',interface,check=False)
        rows=json.loads(link.stdout) if link.returncode==0 else []
        same=bool(rows and rows[0].get('permaddr',rows[0].get('address','')).lower()==owner['mac'].lower())
        current = run('nmcli', '-g', 'GENERAL.CON-UUID', 'device', 'show', interface, check=False).stdout.strip() if same else 'foreign'
        if same and current in ('', '--'):
            run('ip', 'link', 'set', 'dev', interface, 'down', check=False)
            run('/usr/sbin/iw', 'dev', interface, 'set', 'type', 'managed', check=False)
            run('nmcli', 'device', 'set', interface, 'managed', 'yes' if owner['managed'] else 'no', check=False)
        for name in ('owner-'+role+'.json', 'pid-'+role, 'hostapd-'+role+'.conf'):
            (self.state/name).unlink(missing_ok=True)
        self.owners.pop(role, None);self.progress.pop(role, None)

    def recover(self):
        for role in ('board', 'external'):
            marker = self.state/('owner-'+role+'.json')
            if not marker.exists():continue
            owner = json.loads(marker.read_text())
            # Kill only our previously recorded binary/config pair after a crash.
            pidfile = self.state/('pid-'+role)
            if pidfile.exists():
                pid = pidfile.read_text().strip()
                if pid.isdigit():
                    try:
                        args = Path('/proc')/pid/'cmdline'
                        parts = args.read_bytes().split(b'\0')
                        if str(self.app/'bin/hostapd').encode() in parts and str(self.state/('hostapd-'+role+'.conf')).encode() in parts:
                            os.kill(int(pid), 15)
                            for _ in range(80):
                                try:current=args.read_bytes().split(b'\0')
                                except OSError:break
                                if str(self.app/'bin/hostapd').encode() not in current:break
                                time.sleep(.1)
                            else:os.kill(int(pid),9)
                    except (OSError, ProcessLookupError):pass
            self.owners[role] = owner
            self.stop(role)

    def apply(self, request):
        with self.lock:
            role = request.get('id')
            card = next((x for x in self.discover(fresh=True) if x['id']==role), None)
            if not card:raise ValueError('找不到此无线网卡')
            if type(request.get('enabled')) is not bool:raise ValueError('热点开关无效')
            previous = self.saved.get(role, {}).copy()
            value = previous.copy();value['enabled'] = request['enabled']
            if value['enabled']:
                if self.country()!='CN' and self.foreign_wireless():
                    raise ValueError('其他无线连接正在使用不同地区设置，请先断开该连接再开启热点')
                ssid = request.get('ssid', '')
                password = request.get('password') or previous.get('password', '')
                if not isinstance(ssid, str) or not 1 <= len(ssid.encode()) <= 32 or any(ord(c)<32 for c in ssid):
                    raise ValueError('热点名称需为 1～32 字节，且不能包含控制字符')
                if not isinstance(password, str) or not 8 <= len(password) <= 63 or any(not 32<=ord(c)<=126 for c in password):
                    raise ValueError('密码需为 8～63 位英文字母、数字或符号')
                band, channel, width = request.get('band'), request.get('channel'), request.get('bandwidth', 0)
                if band not in ('bg','a') or type(channel) is not int or type(width) is not int or width not in (0,20,40,80,160):
                    raise ValueError('频段、信道或频宽无效')
                allowed = [c for c in card['channels'][band] if width in (0,20) or width in card['channel_widths'][band].get(str(c), [])]
                if not card['supports_ap'] or not allowed or channel and channel not in allowed:
                    raise ValueError('当前驱动不允许所选的 AP 信道或频宽')
                if width==0 and not channel:
                    wide=[c for c in allowed if 80 in card['channel_widths'][band].get(str(c),[])] if band=='a' else []
                    if wide:allowed=wide
                    immediate=[c for c in allowed if c not in card['dfs_channels']]
                    if immediate:allowed=immediate
                subnet = ipaddress.ip_interface(card['subnet']).network
                for item in json.loads(run('ip','-j','-4','addr').stdout):
                    if item['ifname'] in (card['interface'], 'pcat-ap-'+('b' if role=='board' else 'e')):continue
                    for addr in item.get('addr_info', []):
                        if subnet.overlaps(ipaddress.ip_interface(str(addr['local'])+'/'+str(addr['prefixlen'])).network):
                            raise ValueError('热点子网与现有网络冲突')
                actual = channel or choose(allowed,band,'')
                effective=width or (80 if band=='a' and 80 in card['channel_widths'][band].get(str(actual),[]) else 20)
                value.update(ssid=ssid,password=password,band=band,channel=channel,bandwidth=width,effective_width=effective,actual_channel=actual,mac=card['mac'])
            self.stop(role);self.errors.pop(role, None)
            try:
                if value['enabled']:self.start(card,value)
                self.saved[role] = value
                atomic(self.settings,json.dumps(self.saved,ensure_ascii=False))
            except Exception as error:
                self.errors[role] = str(error)
                if previous.get('enabled'):
                    try:self.start(card,previous)
                    except Exception:self.errors[role] += '；原热点恢复失败'
                raise

    def start_saved(self):
        with self.lock:
            self.migrate_legacy()
            try:
                self.extension.restore()
                self.extension.prepare()
            except Exception as error:
                self.extension.notice = str(error)
                if self.extension.record.exists():
                    self.errors['external'] = '增强驱动恢复未完成：'+str(error)
                    return
            country = self.country()
            if country=='CN' or not self.foreign_wireless():
                if not self.country_file.exists() and country:atomic(self.country_file, country)
                run('/usr/sbin/iw','reg','set','CN',check=False)
                time.sleep(1)
            else:
                self.extension.notice = '其他无线连接正在使用，保留系统地区设置。'
            cards = self.discover(fresh=True)
            self.seed_defaults(cards)
            for card in cards:
                role = card['id'];value = self.saved.get(role,{})
                if value.get('enabled'):
                    try:
                        if value.get('mac','').lower()!=card['mac'].lower():raise ValueError('无线网卡已变化，请重新应用热点设置')
                        self.apply(dict(value,id=role))
                    except Exception as error:self.errors[role] = str(error)

    def seed_defaults(self, cards):
        defaults = json.loads((self.app/'defaults.json').read_text())
        changed = False
        for card in cards:
            role = card['id']
            if role not in self.saved and role in defaults:
                self.saved[role] = dict(defaults[role],mac=card['mac'])
                changed = True
        if changed:atomic(self.settings,json.dumps(self.saved,ensure_ascii=False))

    def migrate_legacy(self):
        """Import only the two exact profiles created by our previous app."""
        for card in self.discover(fresh=True):
            role = card['id']
            path = Path('/etc/NetworkManager/system-connections')/('photonicat-ap-'+role+'.nmconnection')
            if not path.exists():continue
            parser = configparser.ConfigParser(interpolation=None)
            parser.read(path)
            identifier = str(uuid.uuid5(uuid.NAMESPACE_DNS,'photonicat.ap.'+role))
            if (parser.get('connection','uuid',fallback='') != identifier or
                parser.get('wifi','mode',fallback='') != 'ap' or
                parser.get('wifi','mac-address',fallback='').lower() != card['mac'].lower()):continue
            backup = self.data/('legacy-backup-'+role+'.nmconnection')
            if not backup.exists():atomic(backup,path.read_text())
            enabled = parser.getboolean('connection','autoconnect',fallback=False)
            if role not in self.saved:
                def unescape(text):
                    return re.sub(r'\\([snt\\r])',lambda m:{'s':' ','n':'\n','t':'\t','r':'\r','\\':'\\'}[m[1]],text)
                ssid = parser.get('wifi','ssid',fallback='')
                if re.fullmatch(r'(?:\d+;)+',ssid):ssid=bytes(int(x) for x in ssid.split(';') if x).decode('utf-8')
                else:ssid=unescape(ssid)
                self.saved[role] = dict(enabled=enabled,ssid=ssid,password=unescape(parser.get('wifi-security','psk',fallback='')),
                    band=parser.get('wifi','band',fallback='bg'),channel=0 if parser.get('user','photonicat.channel',fallback='')=='auto' else parser.getint('wifi','channel',fallback=0),
                    bandwidth=parser.getint('user','photonicat.bandwidth',fallback=0),mac=card['mac'])
                atomic(self.settings,json.dumps(self.saved,ensure_ascii=False))
            current = run('nmcli','-g','GENERAL.CON-UUID','device','show',card['interface'],check=False).stdout.strip()
            if current==identifier:run('nmcli','--wait','20','connection','down','uuid',identifier)
            if enabled:
                # Preserve the profile and original bytes for rollback; prevent
                # old and new AP controllers from autoconnecting simultaneously.
                import io
                parser.set('connection','autoconnect','false')
                output=io.StringIO();parser.write(output);atomic(path,output.getvalue())
                run('nmcli','connection','load',str(path))

    def shutdown(self):
        with self.lock:
            for role in list(self.owners):self.stop(role)
            self.extension.restore()
            if self.country_file.exists():
                original = self.country_file.read_text().strip()
                if re.fullmatch(r'[A-Z0-9]{2}',original) and self.country()=='CN' and original!='CN' and not self.foreign_wireless():
                    run('/usr/sbin/iw','reg','set',original,check=False)
                self.country_file.unlink(missing_ok=True)
