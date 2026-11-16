"""Stage portable QEMU and an ARM64 Linux kernel inside the experiment directory."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tarfile
from urllib.request import urlopen

from prepare_android import ARTIFACTS, digest

KERNEL = ARTIFACTS / 'kernel'
DOWNLOADS = KERNEL / 'downloads'
BIN = KERNEL / 'official'
WINDOWS_QEMU = 'https://qemu.weilnetz.de/w64/2025/'
QEMU_INSTALLER = 'qemu-w64-setup-20251224.exe'
QEMU_INSTALLER_SHA512 = ('f37d9ee8f498879b7ca92a9959d30ce7971f9894f584de574f2ac6f5ba925a7265'
                         'e9d8556d0f56bc75e4e384fc7cac6baed78a89c2e42cdbef109724f01ec16c')
QEMU_BINARY_SHA256 = '90564523aba7e8dba75d8ee9855527f1ff0887b82c98d69035fdfbd782782c39'
ALPINE = 'https://dl-cdn.alpinelinux.org/alpine/v3.22/main/aarch64/'
EXECUTABLE = 'qemu-system-aarch64.exe'


def sha512(path):
    checksum = hashlib.sha512()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            checksum.update(chunk)
    return checksum.hexdigest()


def download(url, name, expected_sha512=None):
    path = DOWNLOADS / name
    if path.is_file() and (expected_sha512 is None or sha512(path) == expected_sha512):
        return path
    temporary = path.with_suffix(path.suffix + '.tmp')
    with urlopen(url, timeout=40) as remote, temporary.open('wb') as output:
        shutil.copyfileobj(remote, output)
    if expected_sha512 and sha512(temporary) != expected_sha512:
        raise ValueError(f'Package SHA-512 differs: {name}')
    temporary.replace(path)
    return path


def portable_qemu(seven_zip):
    url = WINDOWS_QEMU + QEMU_INSTALLER
    archive = download(url, QEMU_INSTALLER, QEMU_INSTALLER_SHA512)
    subprocess.run([str(seven_zip), 'x', '-y', '-bso0', '-bsp0', f'-o{BIN}', str(archive),
                    EXECUTABLE, '*.dll', r'share\efi-virtio.rom', r'-x!$PLUGINSDIR\*'],
                   check=True, timeout=90)
    binary = BIN / EXECUTABLE
    binary_sha256 = digest(binary)
    if binary_sha256 != QEMU_BINARY_SHA256:
        raise ValueError('Extracted QEMU executable SHA-256 differs')
    try:
        completed = subprocess.run([str(binary), '-version'], cwd=BIN, check=True,
                                   capture_output=True, text=True, timeout=15)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError('The staged QEMU executable did not complete its Windows startup check') from exc
    return {'version': completed.stdout.splitlines()[0], 'archive': url,
            'archive_sha512': QEMU_INSTALLER_SHA512, 'binary_sha256': binary_sha256}


def linux_image():
    apk_index = download(ALPINE + 'APKINDEX.tar.gz', 'alpine-v3.22-aarch64-APKINDEX.tar.gz')
    with tarfile.open(apk_index) as archive:
        index = archive.extractfile('APKINDEX').read().decode('utf-8')
    entry = next(row for row in index.split('\n\n') if '\nP:linux-virt\n' in row)
    version = next(line[2:] for line in entry.splitlines() if line.startswith('V:'))
    name = f'linux-virt-{version}.apk'
    package = download(ALPINE + name, name)
    with tarfile.open(package, mode='r:gz', ignore_zeros=True) as archive:
        image = archive.extractfile('boot/vmlinuz-virt').read()
    if image[4:8] == b'zimg':
        offset, size = struct.unpack_from('<II', image, 8)
        if offset + size > len(image) or image[24:28] != b'gzip':
            raise ValueError('Unsupported EFI zboot kernel payload')
        image = image[offset:offset + size]
    if image.startswith(b'\x1f\x8b'):
        image = gzip.decompress(image)
    if image[56:60] != b'ARM\x64':
        raise ValueError('Kernel package does not contain an ARM64 Linux Image')
    (KERNEL / 'Image').write_bytes(image)
    return {'version': version, 'archive': name, 'archive_sha256': digest(package),
            'image_sha256': hashlib.sha256(image).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seven-zip', type=Path, help='path to 7z.exe with NSIS extraction support')
    args = parser.parse_args()
    program_files = Path(os.environ.get('ProgramFiles', 'C:/Program Files'))
    candidates = [args.seven_zip] if args.seven_zip else [
        KERNEL / 'unpacker/7z.exe', Path(shutil.which('7z.exe') or '7z.exe'),
        program_files / '7-Zip/7z.exe',
    ]
    seven_zip = next((path.resolve() for path in candidates if path.is_file()), None)
    if seven_zip is None:
        parser.error('7z.exe is required to extract the Windows QEMU archive')
    for path in (DOWNLOADS, BIN):
        path.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        qemu = pool.submit(portable_qemu, seven_zip)
        kernel = pool.submit(linux_image)
        report = {'qemu': qemu.result(), 'linux': kernel.result()}
    (KERNEL / 'manifest.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(report['qemu']['version'])
    print('Linux', report['linux']['version'])


if __name__ == '__main__':
    main()
