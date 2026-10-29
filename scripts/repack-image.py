#!/usr/bin/env python3
"""Patch only a loop-mounted, checksum-pinned FnNAS image on a build runner."""
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
import re

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import importlib.util
spec = importlib.util.spec_from_file_location('dtb_patch', ROOT / 'scripts/patch-dtb.py')
dtb_patch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dtb_patch)
router_spec = importlib.util.spec_from_file_location('install_router', ROOT / 'scripts/install-router.py')
router = importlib.util.module_from_spec(router_spec)
router_spec.loader.exec_module(router)

def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)

def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            digest.update(block)
    return digest.hexdigest()

@contextmanager
def image_workspace(parent):
    directory = Path(tempfile.mkdtemp(prefix='pcat-fnnas-', dir=parent))
    try:
        yield directory
    finally:
        # Never recurse into a failed unmount, especially a /dev bind mount.
        prefix = str(directory) + '/'
        points = []
        for line in Path('/proc/self/mountinfo').read_text().splitlines():
            point = re.sub(r'\\([0-7]{3})', lambda match: chr(int(match[1], 8)), line.split()[4])
            if point == str(directory) or point.startswith(prefix):
                points.append(point)
        if points:
            print(f'Preserving mounted workspace for cleanup: {directory}', file=sys.stderr)
        else:
            shutil.rmtree(directory)

def main():
    assert sys.platform == 'linux' and os.geteuid() == 0, 'Run on a disposable privileged Linux builder'
    assert os.uname().machine == 'aarch64', 'Native ARM64 is needed for the target mkinitramfs'
    compressed, module_dir, output_dir = map(lambda p: Path(p).resolve(), sys.argv[1:])
    lock = json.loads((ROOT / 'sources.lock.json').read_text())
    target = lock['target']
    assert sha(compressed) == target['image']['sha256']
    module = module_dir / 'dwmac-rk.ko'
    report = json.loads((module_dir / 'build.json').read_text())
    assert sha(module) == report['module_sha256']
    assert report['kernel_release'] == target['kernel_release']
    assert shutil.disk_usage(compressed.parent).free > 12*1024**3, 'At least 12 GiB free working space required'
    output_dir.mkdir(parents=True, exist_ok=True)
    with image_workspace(compressed.parent) as directory:
        work = Path(directory)
        image = work / 'firmware.img'
        with gzip.open(compressed, 'rb') as source, image.open('wb') as output:
            shutil.copyfileobj(source, output, 1024*1024)
        boot_region = image.open('rb').read(16*1024*1024)
        assert boot_region[510:512] == b'\x55\xaa', 'Missing partition-table signature'
        loop = subprocess.check_output(['losetup', '--find', '--show', '--partscan', str(image)], text=True).strip()
        assert loop.startswith('/dev/loop')
        mounted = []
        root = work / 'root'
        root.mkdir()
        try:
            for suffix, filesystem, mountpoint in [('p2', 'btrfs', root), ('p1', 'ext4', root/'boot')]:
                actual = subprocess.check_output(['blkid', '-s', 'TYPE', '-o', 'value', loop+suffix], text=True).strip()
                assert actual == filesystem, f'Unexpected {suffix} filesystem: {actual}'
                mountpoint.mkdir(exist_ok=True)
                run('mount', '-t', filesystem, loop+suffix, str(mountpoint))
                mounted.append(mountpoint)
            release = target['kernel_release']
            installed = root / f'usr/lib/modules/{release}/kernel/drivers/net/ethernet/stmicro/stmmac/dwmac-rk.ko'
            dtb = root / 'boot/dtb/rockchip/rk3568-photonicat.dtb'
            assert sha(installed) == target['original_module_sha256'], 'Image module differs from reviewed kernel package'
            assert sha(dtb) == target['original_dtb_sha256'], 'Image DTB differs from reviewed kernel package'
            backup = root / 'usr/lib/photonicat-adaptation/original'
            backup.mkdir(parents=True, exist_ok=True)
            shutil.copy2(installed, backup/'dwmac-rk.ko')
            shutil.copy2(dtb, backup/dtb.name)
            shutil.copy2(module, installed)
            # The base image ships the optional trim/alternatives link before
            # creating its target directory. Keep this extension point intact.
            optional_link = root / f'usr/lib/modules/{release}/updates/trim/alternatives'
            optional_target = f'/usr/trim/modules/{release}/alternatives'
            assert optional_link.is_symlink() and os.readlink(optional_link) == optional_target, 'Unexpected trim module layout'
            present = subprocess.run(['chroot', str(root), '/usr/bin/test', '-d', optional_target]).returncode
            assert present in (0, 1), 'Cannot inspect optional module directory'
            if present == 1:
                run('chroot', str(root), '/usr/bin/install', '-d', '-m', '0755', optional_target)
                print('Created missing upstream optional trim module directory', flush=True)
            report['created_optional_module_directory'] = present == 1
            # FnNAS has absolute links to /usr/trim/modules. Resolve them in
            # the target root, never against the build runner's filesystem.
            dependency = run('chroot', str(root), '/usr/bin/env', '-i',
                             'PATH=/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL=C',
                             'depmod', '-a', release, capture_output=True, text=True)
            print(dependency.stdout, end='')
            print(dependency.stderr, end='', file=sys.stderr)
            assert 'ERROR:' not in dependency.stderr, 'Target module dependency generation reported errors'
            dtb_report = dtb_patch.patch(dtb, target['original_dtb_sha256'])
            report['router'] = router.install(root)
            (root/'usr/local/sbin').mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT/'scripts/collect-network.sh', root/'usr/local/sbin/photonicat-diagnostics')
            (root/'usr/local/sbin/photonicat-diagnostics').chmod(0o755)
            for source, destination in [('/dev', root/'dev'), ('/sys', root/'sys')]:
                destination.mkdir(exist_ok=True)
                run('mount', '--bind', source, str(destination))
                mounted.append(destination)
            (root/'proc').mkdir(exist_ok=True)
            run('mount', '-t', 'proc', 'proc', str(root/'proc'))
            mounted.append(root/'proc')
            initrd = root / f'boot/initrd.img-{release}'
            uinitrd = root / f'boot/uInitrd-{release}'
            old_header = uinitrd.read_bytes()[:64]
            assert old_header[:4] == b'\x27\x05\x19\x56', 'Unexpected uInitrd format'
            arch = {2: 'arm', 22: 'arm64'}[old_header[29]]
            module_list = root/'etc/initramfs-tools/modules'
            content = module_list.read_text() if module_list.exists() else ''
            for name in ('dwmac-rk', 'motorcomm', 'pcs_xpcs'):
                if name not in content.splitlines():
                    content += '\n' + name + '\n'
            module_list.parent.mkdir(parents=True, exist_ok=True)
            module_list.write_text(content)
            run('chroot', str(root), '/usr/bin/env', '-i', 'PATH=/usr/sbin:/usr/bin:/sbin:/bin', 'HOME=/root', 'LC_ALL=C',
                'mkinitramfs', '-o', f'/boot/initrd.img-{release}', release)
            run('mkimage', '-A', arch, '-O', 'linux', '-T', 'ramdisk', '-C', 'none', '-n', 'uInitrd', '-d', str(initrd), str(uinitrd))
            # Verify the early-boot copy too, so the old module cannot shadow the fixed rootfs module.
            extracted = work/'initramfs'
            run('unmkinitramfs', str(initrd), str(extracted))
            early = list(extracted.rglob('dwmac-rk.ko'))
            assert early and all(sha(path) == report['module_sha256'] for path in early), 'Stale or missing early-boot driver'
            naming_rules = list(extracted.rglob('70-photonicat-net.rules'))
            assert naming_rules and all(sha(path) == sha(ROOT/'files/etc/udev/rules.d/70-photonicat-net.rules')
                                        for path in naming_rules), 'Missing early-boot LAN/WAN naming rules'
            report.update({'dtb': dtb_report, 'base_image_sha256': target['image']['sha256'],
                           'status': 'test_image_not_hardware_verified', 'board': 'photonicat',
                           'early_boot_driver_verified': True})
            network_test = work / 'network-test'
            network_test.mkdir()
            run('unshare', '--mount', '--net', '--pid', '--fork', '--kill-child',
                '/usr/bin/env', 'PCAT_NETWORK_SANDBOX=1', 'python3', str(ROOT/'tests/router-integration.py'),
                '--sandbox', str(root), str(network_test))
            report['router_integration'] = json.loads((network_test/'result.json').read_text())
            (root/'usr/lib/photonicat-adaptation/build.json').write_text(json.dumps(report, indent=2)+'\n')
            (output_dir/'build.json').write_text(json.dumps(report, indent=2)+'\n')
            run('sync')
        finally:
            for mountpoint in reversed(mounted):
                subprocess.run(['umount', str(mountpoint)], check=False)
            # Refuse to detach/clean a still-mounted image.
            remaining = [path for path in mounted if subprocess.run(['mountpoint', '-q', str(path)]).returncode == 0]
            if remaining:
                raise RuntimeError(f'Build image is still mounted: {remaining}')
            run('losetup', '-d', loop)
        with image.open('rb') as stream:
            assert stream.read(16*1024*1024) == boot_region, 'Bootloader/partition table changed'
        report['bootloader_and_partition_table_unchanged'] = True
        (output_dir/'build.json').write_text(json.dumps(report, indent=2)+'\n')
        name = 'fnnas-photonicat1-1253-6.18.18-test.img.gz'
        final = output_dir / name
        with image.open('rb') as source, final.open('wb') as destination:
            with gzip.GzipFile(filename='', fileobj=destination, mode='wb', compresslevel=6, mtime=0) as output:
                shutil.copyfileobj(source, output, 1024*1024)
        (output_dir/'sha256sums').write_text(f'{sha(final)}  {name}\n')
        print(f'Built test image: {final}')

if __name__ == '__main__':
    main()
