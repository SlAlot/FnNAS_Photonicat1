"""AP capabilities and channel plans; no device or configuration writes."""
import re

ROLES = {'ath10k_sdio': ('board', '板载 QCA9377', '192.168.18.1/24'),
         'ath11k_pci': ('external', '外接 QCNFA765', '192.168.19.1/24')}


def plan(allowed, band, channel, width):
    if channel not in allowed:
        raise ValueError('所选信道当前不可用于热点')
    frequency = 2407 + channel * 5 if band == 'bg' else 5000 + channel * 5
    if width in (0, 20):
        return 0, frequency
    if width == 40:
        for offset in ((1, -1) if band == 'bg' and channel <= 7 else (-1, 1)):
            other = channel + offset * 4
            if other in allowed and (band == 'bg' or min(channel, other) % 8 == (5 if channel >= 149 else 4)):
                return offset, frequency + offset * 10
    if band == 'a' and width in (80, 160):
        starts = (36, 52, 100, 116, 132, 149, 165) if width == 80 else (36, 100, 149)
        for start in starts:
            group = list(range(start, start + width // 5, 4))
            if channel in group and all(value in allowed for value in group):
                return (1 if group.index(channel) % 2 == 0 else -1), 5000 + start * 5 + width // 2 - 10
    raise ValueError('所选信道没有足够的可用频宽')


def capabilities(info):
    channels = {'bg': [], 'a': []}
    widths = {'bg': {}, 'a': {}}
    modes, dfs = {}, []
    radar = re.search(r'radar detect widths:\s*\{([^}]+)', info)
    radar_widths = {int(x) for x in re.findall(r'(20|40|80|160) MHz', radar[1])} if radar else set()
    for section in re.split(r'\n\s*Band \d+:', info)[1:]:
        rows = []
        for line in section.splitlines():
            match = re.search(r'\* (\d+) MHz \[(\d+)\]', line)
            if not match:
                continue
            freq, channel = map(int, match.groups())
            band = 'bg' if 2400 <= freq < 2500 else 'a' if 5000 <= freq < 5900 else None
            if not band or 'disabled' in line:
                continue
            is_dfs = 'radar detection' in line
            # A no-IR DFS channel can only be used through a real CAC-capable driver.
            if 'no IR' in line and not (is_dfs and radar_widths):
                continue
            if is_dfs and not radar_widths:
                continue
            channels[band].append(channel)
            rows.append((band, channel, line, is_dfs))
            if is_dfs:
                dfs.append(channel)
        for band, channel, flags, is_dfs in rows:
            ap_he='\n'.join(block for block in re.split(r'\n\s*HE Iftypes:',section)[1:]
                            if re.search(r'\bAP\b',block.splitlines()[0]))
            he = bool(ap_he)
            vht = 'VHT Capabilities' in section
            modes[band] = {'ht': 'HT20/HT40' in section, 'vht': vht, 'he': he}
            ht_part = section.split('VHT Capabilities')[0]
            modes[band]['ht_flags'] = [flag for text,flag in (
                ('RX LDPC','LDPC'),('RX HT20 SGI','SHORT-GI-20'),('RX HT40 SGI','SHORT-GI-40'),
                ('TX STBC','TX-STBC'),('RX STBC 1-stream','RX-STBC1')) if text in ht_part]
            vht_flags = []
            match = re.search(r'VHT Capabilities \(0x([0-9a-fA-F]+)\)',section)
            if match:
                cap = int(match[1],16)
                for mask,flag in ((1<<4,'RXLDPC'),(1<<5,'SHORT-GI-80'),(1<<6,'SHORT-GI-160'),(1<<7,'TX-STBC-2BY1')):
                    if cap & mask:vht_flags.append(flag)
                if (cap>>8)&7:vht_flags.append('RX-STBC-1')
                if cap&3 in (1,2):vht_flags.append('MAX-MPDU-'+('7991' if cap&3==1 else '11454'))
                vht_flags.append('MAX-A-MPDU-LEN-EXP'+str((cap>>23)&7))
            if 'Supported Channel Width: 160 MHz' in section:vht_flags.append('VHT160')
            modes[band]['vht_flags'] = vht_flags
            possible = [20]
            if modes[band]['ht']:
                possible.append(40)
            if band == 'a' and (vht or he):
                possible.append(80)
                if 'Supported Channel Width: 160 MHz' in section or 'HE160/5GHz' in ap_he:
                    possible.append(160)
            choices = []
            for width in possible:
                try:
                    offset, center = plan(channels[band], band, channel, width)
                    if width >= 40 and ('no HT40+' if offset > 0 else 'no HT40-') in flags:
                        continue
                    if width >= 80 and 'no 80MHz' in flags or width == 160 and 'no 160MHz' in flags:
                        continue
                    block = [c for c in channels[band] if abs((2407 if band == 'bg' else 5000) + 5*c - center) < width / 2]
                    block_flags=[line for row_band,c,line,_ in rows if row_band==band and c in block]
                    if width>=80 and any('no 80MHz' in line for line in block_flags):continue
                    if width==160 and any('no 160MHz' in line for line in block_flags):continue
                    if any(c in dfs for c in block) and width not in radar_widths:
                        continue
                    choices.append(width)
                except ValueError:
                    pass
            widths[band][str(channel)] = choices
    return {'channels': channels, 'channel_widths': widths, 'modes': modes, 'dfs_channels': dfs,
            'supports_ap': bool(re.search(r'^\s+\* AP\s*$', info, re.M))}


def choose(allowed, band, scan):
    preferred = [c for c in (1, 6, 11) if c in allowed] if band == 'bg' else allowed
    candidates = preferred or allowed
    observations = []
    for block in re.split(r'^BSS ', scan, flags=re.M)[1:]:
        freq = re.search(r'^\s*freq:\s*(\d+)', block, re.M)
        signal = re.search(r'^\s*signal:\s*(-?[\d.]+)', block, re.M)
        if freq and signal:
            observations.append((int(freq[1]), float(signal[1])))
    def score(channel):
        f = (2407 if band == 'bg' else 5000) + channel * 5
        return sum(max(0, 1-abs(other-f)/(25 if band == 'bg' else 80)) * 10**(min(0, power)/10)
                   for other, power in observations)
    return min(candidates, key=lambda c: (score(c), candidates.index(c)))
