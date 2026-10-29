#!/usr/bin/env python3
"""Adapt the pinned NAS editor to a dedicated DHCP-serving LAN role."""
import hashlib
import json
from pathlib import Path
import sys

ASSET = 'usr/trim/www/assets/Setting-CbdHMm55.js'
ORIGINAL_SHA256 = 'b5aed22755fea83e4659a7a40fdf9d75dcf38a37f464b935fa65762fdc27ccb0'
HELPER = '''function pcatLanSettings(value){return value.ifName==="pcatlan0"?{...value,ipv4Dhcp:false,defaultIpv4Gateway:false,ipv4Gateway:""}:value;}
function pcatBondCandidates(ports){return ports.filter(port=>port.name!=="pcatlan0");}
async function pcatHardwareInfo(native){if(native?.gpu?.length)return native;try{const response=await fetch("/photonicat-platform/hardware.json",{cache:"no-store",signal:AbortSignal.timeout(2000)});if(!response.ok)return native;const extra=await response.json();if(extra?.gpu?.length)return {...native,gpu:extra.gpu};}catch{}return native;}
'''


def replace_once(source, old, new):
    if source.count(old) != 1:
        raise ValueError('Unexpected network editor source context: ' + old[:100])
    return source.replace(old, new, 1)


def adapt(source):
    source = replace_once(source, 'const h=c(x,f);return x.ifType===vt.WIRELESS?',
        'const h=c(x,f);if(x.ifType===vt.WIRELESS)return t.jsx(me.Menu,{children:t.jsx(me.Item,{onClick:()=>window.open("/photonicat-hotspot/","_blank"),children:"热点管理"})});return x.ifType===vt.WIRELESS?')
    source = replace_once(source, 'return U(async(c,u)=>new Promise((d,m)=>{const p=()=>{var h;(h=o.current)',
        'return U(async(c,u)=>new Promise((d,m)=>{if(c?.ifType===vt.WIRELESS){window.open("/photonicat-hotspot/","_blank");d(void 0);return}if(c?.name==="pcatlan0"){Y.info({title:"LAN 管理口",getPopupContainer:()=>s.current,content:"此网口专用于电脑直连、DHCP 和飞牛管理。管理地址："+(c.ipv4Addr||c.ipv4?.[0]||"192.168.17.1")+"。为避免失联，不通过普通网络设置修改此端口；5G 和 WAN 口仍可正常设置。",onOk:()=>d(void 0),onCancel:()=>d(void 0)});return}const p=()=>{var h;(h=o.current)')
    source = replace_once(source, 'return U((o,i,c)=>{Y.info({title:`${i} | ${o.name}`,icon:null,width:442,',
        'return U((o,i,c)=>{if(o.ifType===vt.WIRELESS){window.open("/photonicat-hotspot/","_blank");c?.();return}Y.info({title:`${i} | ${o.name}`,icon:null,width:442,')
    source = replace_once(source, 'content:t.jsx(_N,{networkBondList:i,selectNet:a,',
                          'content:t.jsx(_N,{networkBondList:pcatBondCandidates(i),selectNet:a,')
    source = replace_once(source, 't.jsx(me.Item,{onClick:()=>{n(x,f)},children:u("networkSettings.edit")})',
                          't.jsx(me.Item,{onClick:()=>{n(x,f)},children:x.name==="pcatlan0"?"管理口说明":u("networkSettings.edit")})')
    source = replace_once(source, 'const m=await O.sysinfo.getHardwareInfo();a(p=>{p.hardwareInfo=m.data})',
                          'const m=await O.sysinfo.getHardwareInfo(),pcatInfo=await pcatHardwareInfo(m.data);a(p=>{p.hardwareInfo=pcatInfo})')
    source = replace_once(source, 'const v=JSON.parse(JSON.stringify(Object.assign(b)));',
                          'const v=pcatLanSettings(JSON.parse(JSON.stringify(Object.assign(b))));')
    source = replace_once(source, '{formApi:s,networkPortData:n,ssid:a}=$a();return t.jsx("div",{className:"",',
                          '{formApi:s,networkPortData:n,ssid:a,defaultValue:pcatDefaults}=$a();return t.jsx("div",{className:"",')
    source = replace_once(source, 't.jsx(A.Radio,{value:!0,children:e("networkSettings.autoDhcp")})',
                          't.jsx(A.Radio,{value:!0,disabled:pcatDefaults?.ifName==="pcatlan0",children:e("networkSettings.autoDhcp")})')
    source = replace_once(source, 't.jsx(A.Radio,{value:!1,children:e("networkSettings.manualSetting")})',
                          't.jsx(A.Radio,{value:!1,children:pcatDefaults?.ifName==="pcatlan0"?"LAN 地址（提供 DHCP）":e("networkSettings.manualSetting")})')
    source = replace_once(source, 't.jsx(A.Checkbox,{field:"defaultIpv4Gateway",noLabel:!0,children:',
                          't.jsx(A.Checkbox,{field:"defaultIpv4Gateway",disabled:pcatDefaults?.ifName==="pcatlan0",noLabel:!0,children:')
    source = replace_once(source, 'rules:[{validator:p("ipv4Gateway")}],disabled:d,field:"ipv4Gateway"',
                          'rules:[{validator:p("ipv4Gateway")}],disabled:d||o?.ifName==="pcatlan0",field:"ipv4Gateway"')
    source = replace_once(source, 't.jsxs(me.Item,{onClick:()=>r(x),children:[x.isOvsPort?',
                          't.jsxs(me.Item,{disabled:x.name==="pcatlan0",onClick:()=>r(x),children:[x.isOvsPort?')
    return HELPER + source


def patch(root):
    root = Path(root)
    path = root / ASSET
    original = path.read_bytes()
    assert hashlib.sha256(original).hexdigest() == ORIGINAL_SHA256, 'NAS editor changed; review before patching'
    source = adapt(original.decode('utf-8'))
    version = hashlib.sha256(source.encode()).hexdigest()[:12]
    index = root / 'usr/trim/www/index.html'
    html = index.read_text(encoding='utf-8')
    assert html.count('<head>') == 1 and 'id="photonicat-network-imports"' not in html
    # An import map busts only the modified settings chunk's immutable cache.
    # Its imports still resolve to the original, single shared core module.
    custom_asset = 'photonicat-' + path.name
    imports = {'imports': {'/assets/' + path.name: '/assets/' + custom_asset + '?v=' + version}}
    mapping = '<script type="importmap" id="photonicat-network-imports">' + json.dumps(imports) + '</script>'
    html = html.replace('<head>', '<head>' + mapping, 1)
    # Native web files are recovered and monitored by the vendor's nginx.
    # Keep them byte-for-byte intact; serve only our adapter through its conf.d extension.
    destination = root / 'usr/local/share/photonicat-web'
    destination.mkdir(parents=True, exist_ok=True)
    (destination / custom_asset).write_text(source, encoding='utf-8', newline='\n')
    (destination / 'index.html').write_text(html, encoding='utf-8', newline='\n')
    config = destination / 'nginx.conf'
    lines = []
    for route in ('/', '/index', '/index.html', '/signin', '/login', '/system', '/oauth'):
        lines.append('location = ' + route + ' {\n'
                     '    root /usr/local/share/photonicat-web;\n'
                     '    try_files /index.html =404;\n'
                     '    default_type text/html;\n'
                     '    add_header Cache-Control no-store;\n}\n')
    lines.append('location = /assets/' + custom_asset + ' {\n'
                 '    alias /usr/local/share/photonicat-web/' + custom_asset + ';\n'
                 '    default_type application/javascript;\n'
                 '    add_header Cache-Control no-cache;\n}\n')
    lines.append('location = /photonicat-platform/hardware.json {\n'
                 '    alias /run/photonicat-hardware/hardware.json;\n'
                 '    default_type application/json;\n'
                 '    add_header Cache-Control no-store;\n}\n')
    config.write_text('\n'.join(lines), encoding='utf-8', newline='\n')
    (destination / 'manifest.json').write_text(json.dumps({
        'asset': path.name, 'asset_sha256': ORIGINAL_SHA256,
        'index_sha256': hashlib.sha256(index.read_bytes()).hexdigest()}) + '\n', encoding='utf-8')
    return {'editor_sha256': hashlib.sha256(source.encode()).hexdigest(), 'import_map_version': version,
            'native_editor_preserved': path.read_bytes() == original}


if __name__ == '__main__':
    print(json.dumps(patch(sys.argv[1]), indent=2))
