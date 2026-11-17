"""Install verified artwork and roster packs from the public resource release."""

from http.client import HTTPException
import json
from pathlib import Path
import re
from shutil import copyfileobj
from tempfile import TemporaryDirectory
from time import sleep
from urllib.error import URLError
from urllib.request import urlopen
from zipfile import BadZipFile, ZipFile

from prepare_android import ARTIFACTS, digest


DESCRIPTOR = 'battle_ui_assets/art-update.json'
ROSTER_DESCRIPTOR = 'battle_ui_assets/roster-update.json'
REPOSITORY = 'Torfaen/gget-battle-simulator-resources'
ART_PATH = re.compile(r'(?:battle_ui_assets/(?:pilot|supporter|unit)_portraits/[A-Za-z0-9_]+\.png|'
                      r'idle_koma/[A-Za-z0-9_]+/(?:front|back|left|right)\.png|idle_koma/unit_index\.json|'
                      r'battle_ui_assets/maps/[0-9]+(?:\.json|(?:-camera)?\.webp)|'
                      r'roster_update/(?:(?:base|updated|OctoCatalog)\.bytes|'
                      r'(?:names|manifest|stage_catalog)\.json|stages/[0-9]+/(?:StageEntity\.json|NpcExpected\.tsv|manifest\.json)))')


def matches(path, checksum):
    return path.is_file() and digest(path) == checksum


def under(directory, relative):
    path = directory / relative
    if not path.resolve().is_relative_to(directory.resolve()):
        raise ValueError(f'Asset update would leave its directory: {relative}')
    return path


def read_descriptor(payload):
    descriptor = json.loads(payload)
    if descriptor['schema'] != 1 or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]+', descriptor['tag']):
        raise ValueError('Unsupported art update descriptor')
    seen = set()
    for pack in descriptor['packs']:
        if (not re.fullmatch(r'[A-Za-z0-9_-]+\.zip', pack['asset']) or
                not re.fullmatch(r'[0-9a-f]{64}', pack['sha256']) or not pack['files']):
            raise ValueError('Invalid art update pack')
        for path, checksum in pack['files'].items():
            if (not ART_PATH.fullmatch(path) or path in seen or
                    not re.fullmatch(r'[0-9a-f]{64}', checksum)):
                raise ValueError(f'Invalid or duplicate art update path: {path}')
            seen.add(path)
    return descriptor


def download_pack(tag, asset, checksum, cache, scratch):
    path = under(cache, asset)
    if matches(path, checksum):
        return path
    pending = scratch / asset
    url = f'https://github.com/{REPOSITORY}/releases/download/{tag}/{asset}'
    for attempt in range(1, 4):
        print(f'Downloading resource pack {asset} (attempt {attempt}/3)', flush=True)
        try:
            with urlopen(url, timeout=25) as response, pending.open('wb') as output:
                copyfileobj(response, output, length=1024 * 1024)
            break
        except (URLError, HTTPException, TimeoutError, ConnectionError):
            if attempt == 3:
                raise
            sleep(attempt)
    if not matches(pending, checksum):
        raise ValueError(f'Artwork archive SHA-256 mismatch: {asset}')
    pending.replace(path)
    return path


def install_pack(artifacts, cache, tag, pack):
    expected = pack['files']
    changed = {name: target for name, checksum in expected.items()
               if not matches(target := under(artifacts, name), checksum)}
    if not changed:
        return 0
    with TemporaryDirectory(prefix='art-', dir=cache) as directory:
        scratch = Path(directory)
        path = download_pack(tag, pack['asset'], pack['sha256'], cache, scratch)
        queued = []
        with ZipFile(path) as archive:
            members = archive.namelist()
            if len(members) != len(expected) or set(members) != expected.keys():
                raise ValueError(f'Unexpected contents in {path.name}')
            for relative, target in changed.items():
                staged = under(scratch, relative)
                staged.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(relative) as source, staged.open('wb') as output:
                    copyfileobj(source, output)
                if not matches(staged, expected[relative]):
                    raise ValueError(f'Artwork file SHA-256 mismatch: {relative}')
                queued.append((staged, target))
        for staged, target in sorted(queued, key=lambda pair: pair[1].suffix == '.json'):
            target.parent.mkdir(parents=True, exist_ok=True)
            staged.replace(target)
    return len(changed)


def install_descriptor(artifacts, relative, label):
    path = under(artifacts, relative)
    if not path.is_file():
        return
    try:
        descriptor = read_descriptor(path.read_bytes())
        cache = under(artifacts, 'art-update-cache')
        cache.mkdir(exist_ok=True)
        count = sum(install_pack(artifacts, cache, descriptor['tag'], pack) for pack in descriptor['packs'])
        print(f'{label} update ready: {count} file(s) installed', flush=True)
    except (OSError, HTTPException, ValueError, KeyError, TypeError, AttributeError, BadZipFile) as exc:
        print(f'{label} update pending; starting with the available files: {exc}', flush=True)


def update_release_art(artifacts=ARTIFACTS):
    install_descriptor(artifacts, DESCRIPTOR, 'Artwork')
    install_descriptor(artifacts, ROSTER_DESCRIPTOR, 'Roster')
