#!/usr/bin/env python3
"""Fetch public, pinned inputs; never forward GitHub credentials."""
import argparse
import concurrent.futures
import hashlib
import json
from pathlib import Path
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]

def fetch(url, target, expected, git_blob=False):
    target = Path(target)
    def digest(path):
        if git_blob:
            data = path.read_bytes()
            return hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
        result = hashlib.sha256()
        with path.open('rb') as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b''):
                result.update(chunk)
        return result.hexdigest()
    if target.exists() and digest(target) == expected:
        return
    assert url.startswith('https://'), url
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + '.part')
    for attempt in range(3):
        try:
            request = urllib.request.Request(url, headers={'User-Agent': 'Photonicat-FnNAS-Builder/0.1'})
            with urllib.request.urlopen(request, timeout=60) as response, temporary.open('wb') as output:
                assert response.url.startswith('https://')
                while chunk := response.read(1024 * 1024):
                    output.write(chunk)
            assert digest(temporary) == expected, f'Input checksum mismatch: {target.name}'
            temporary.replace(target)
            print(f'Verified {target.name}', flush=True)
            return
        except Exception:
            if attempt == 2:
                raise
            time.sleep(2)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['kernel', 'image', 'sources'])
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    lock = json.loads((ROOT / 'sources.lock.json').read_text())
    if args.mode == 'sources':
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(fetch, item['url'], args.destination / item['name'], item['blob'], True)
                       for item in lock['linux']['files']]
            for future in futures:
                future.result()
    else:
        item = lock['target'][args.mode]
        fetch(item['url'], args.destination, item['sha256'])
        assert args.destination.stat().st_size == item['size'], 'Input size mismatch'

if __name__ == '__main__':
    main()
