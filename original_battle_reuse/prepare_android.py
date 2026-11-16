"""Extract the original binaries and stage a pinned ARM64 Android userspace."""

from concurrent.futures import ThreadPoolExecutor
from hashlib import sha1, sha256
import json
from pathlib import Path
import shutil
import sys
from urllib.request import urlopen
from zipfile import ZipFile

from runtime_version import PINNED_LIBRARY_SHA256

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from battle_paths import ARTIFACTS

ANDROID = ARTIFACTS / 'android'
REVISION = 'df3fa4dfc0b9d4164f8678699d8923df847eb3d2'

SYSTEM_FILES = {
    'bin/arm64_android_hello': ('arm64_android', 'c8d61cf29bba1efecffb991e84cf8ab1c77ee5bd'),
    'system/bin/linker64': ('arm64_android', '8577fbf4a3e95d66e9004a97c0440d2c09d6e47c'),
    'system/lib64/libc.so': ('arm64_android', '63dc45a4b1d65bba70b943b178e8a8d0670c0da8'),
    'system/lib64/libdl.so': ('arm64_android', '18eac8966a48e6a0c522b881683395337729916c'),
    'system/lib64/liblog.so': ('arm64_android6.0', '68889942f324bc83ee5dc77340ab167164abea56'),
    'system/lib64/libm.so': ('arm64_android6.0', '47290eac08d9fa7f45cd6b94ee5d43a22447a8d2'),
    'system/lib64/libc++.so': ('arm64_android6.0', 'd01fb8e1b95a8a98f9558e69972c610d28320a16'),
}


def git_blob_hash(data):
    return sha1(f'blob {len(data)}\0'.encode() + data).hexdigest()


def prepare_system(rootfs):
    def fetch(item):
        name, (source, expected) = item
        path = rootfs / name
        url = f'https://raw.githubusercontent.com/qilingframework/rootfs/{REVISION}/{source}/{name}'
        if path.exists() and git_blob_hash(path.read_bytes()) == expected:
            return
        with urlopen(url, timeout=30) as response:
            data = response.read()
        if not data.startswith(b'\x7fELF') or git_blob_hash(data) != expected:
            raise ValueError(f'Unexpected upstream artifact: {name}')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(fetch, SYSTEM_FILES.items()))
    (rootfs / 'proc/self').mkdir(parents=True, exist_ok=True)
    (rootfs / 'proc/self/exe').touch()


def extract(archive, names, directory):
    directory.mkdir(parents=True, exist_ok=True)
    with ZipFile(archive) as zipped:
        for member, filename in names.items():
            dest = directory / filename
            if dest.exists() and dest.stat().st_size == zipped.getinfo(member).file_size:
                continue
            temporary = dest.with_suffix(dest.suffix + '.tmp')
            with zipped.open(member) as source, temporary.open('wb') as stream:
                shutil.copyfileobj(source, stream)
            temporary.replace(dest)


def digest(path):
    result = sha256()
    with path.open('rb') as stream:
        for data in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(data)
    return result.hexdigest()


def prepare_game(rootfs):
    manifest = json.loads((ARTIFACTS / 'source/source_manifest.json').read_text('utf-8'))
    provenance = manifest['provenance']
    apk = Path(provenance['apk'])
    game = rootfs / 'game'
    extract(apk, {f'lib/arm64-v8a/{name}': name for name in ('libil2cpp.so', 'libunity.so', 'libmain.so')}, game)
    extract(apk.with_name('base.apk'), {
        'assets/bin/Data/Managed/Metadata/global-metadata.dat': 'global-metadata.packaged.dat',
        'assets/bin/Data/globalgamemanagers': 'globalgamemanagers',
    }, game)
    if digest(game / 'libil2cpp.so') != PINNED_LIBRARY_SHA256:
        raise ValueError('Extracted native library fingerprint differs from the analyzed 2.7.0 version')
    metadata = Path(provenance['metadata'])
    metadata_path = game / 'Data/Managed/Metadata/global-metadata.dat'
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(metadata, metadata_path)
    if digest(metadata_path) != provenance['metadata_sha256']:
        raise ValueError('Decoded metadata fingerprint differs from the analyzed source')
    return {str(path.relative_to(rootfs).as_posix()): digest(path)
            for path in sorted(game.rglob('*')) if path.is_file()}


def main():
    rootfs = ANDROID / 'rootfs'
    prepare_system(rootfs)
    sources = prepare_game(rootfs)
    manifest = {'system_revision': REVISION, 'game_sha256': sources}
    (ANDROID / 'bundle.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
