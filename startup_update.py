"""Apply published code and asset updates before launching the simulator."""

import argparse
from datetime import datetime
import hashlib
from http.client import HTTPException
import json
from pathlib import Path
import re
import shutil
from tempfile import TemporaryDirectory
from urllib.request import urlopen

PROGRAM = 'Torfaen/gget-battle-simulator'
RESOURCES = 'Torfaen/gget-battle-simulator-resources'
MANIFEST_URL = f'https://raw.githubusercontent.com/{RESOURCES}/main/updates.json'
ASSET_ROOTS = ('original_battle_reuse/artifacts/idle_koma/',
               'original_battle_reuse/artifacts/battle_ui_assets/')


def valid_path(path):
    return all(re.fullmatch(r'[A-Za-z0-9_.-]+', part) and part not in ('.', '..')
               and not part.endswith('.') for part in path.split('/'))


def program_path(path):
    return (path in ('battle_paths.py', 'requirements-runtime.txt') or
            (path.startswith(('original_battle_reuse/', 'tools/')) and
             Path(path).suffix in ('.py', '.c', '.h')))


def read_manifest(text):
    manifest = json.loads(text)
    code = manifest['program']
    resources = manifest['resources']
    if (manifest['schema'] != 1 or type(manifest['revision']) is not int or manifest['revision'] < 1 or
            not re.fullmatch(r'[0-9a-f]{40}', code['commit']) or
            not re.fullmatch(r'[A-Za-z0-9_.-]+', resources['tag']) or resources['tag'] in ('.', '..')):
        raise ValueError('Unsupported update manifest')
    files = code['files']
    required = {'battle_paths.py', 'requirements-runtime.txt', 'original_battle_reuse/battle_ui.py',
                'original_battle_reuse/prepare_demo_formation.py', 'original_battle_reuse/prepare_kernel.py'}
    if not required.issubset(files):
        raise ValueError('Incomplete program file list')
    for path, checksum in files.items():
        if not valid_path(path) or not program_path(path) or not re.fullmatch(r'[0-9a-f]{64}', checksum):
            raise ValueError(f'Invalid program file: {path}')
    for path, entry in resources['files'].items():
        if (not valid_path(path) or not path.startswith(ASSET_ROOTS) or Path(path).suffix not in
                ('.png', '.webp', '.json') or not valid_path(entry['asset']) or '/' in entry['asset'] or
                not re.fullmatch(r'[0-9a-f]{64}', entry['sha256'])):
            raise ValueError(f'Invalid resource file: {path}')
    return manifest


def under(root, relative):
    path = root / relative
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError(f'Update path leaves its directory: {relative}')
    return path


def matches(path, checksum):
    try:
        with path.open('rb') as source:
            digest = hashlib.sha256()
            for block in iter(lambda: source.read(1024 * 1024), b''):
                digest.update(block)
        return digest.hexdigest() == checksum
    except OSError:
        return False


def download_file(url, target, checksum):
    with urlopen(url, timeout=20) as response, target.open('wb') as output:
        shutil.copyfileobj(response, output, 1024 * 1024)
    if not matches(target, checksum):
        raise ValueError(f'SHA-256 mismatch: {target.name}')


def prepare_program(project, store, code, previous, log):
    files = code['files']
    if all(matches(under(project, name), checksum) for name, checksum in files.items()):
        return project, 0
    installed = under(store, f'programs/{code["commit"]}')
    count = 0
    for name, checksum in files.items():
        target = under(installed, name)
        if matches(target, checksum):
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + '.part')
        try:
            source = next((path for base in (previous, project) if base
                           and matches(path := under(base, name), checksum)), None)
            if source:
                shutil.copyfile(source, temporary)
            else:
                log(f'Downloading program file {name}')
                download_file(f'https://raw.githubusercontent.com/{PROGRAM}/{code["commit"]}/{name}',
                              temporary, checksum)
                count += 1
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    return installed, count


def install_assets(project, data, store, resources, log):
    pending = []
    count = 0
    with TemporaryDirectory(prefix='assets-', dir=store) as temporary:
        for name, entry in resources['files'].items():
            target = under(data, name)
            if matches(target, entry['sha256']):
                continue
            staged = under(Path(temporary), name)
            staged.parent.mkdir(parents=True, exist_ok=True)
            bundled = under(project, f'.update-files/{entry["asset"]}')
            if matches(bundled, entry['sha256']):
                shutil.copyfile(bundled, staged)
            else:
                log(f'Downloading resource file {name}')
                url = f'https://github.com/{RESOURCES}/releases/download/{resources["tag"]}/{entry["asset"]}'
                download_file(url, staged, entry['sha256'])
                count += 1
            pending.append((staged, target))
        # Install the images before the index starts referring to them.
        for staged, target in sorted(pending, key=lambda pair: pair[1].suffix == '.json'):
            target.parent.mkdir(parents=True, exist_ok=True)
            staged.replace(target)
    return count


def startup(project, data, log):
    store = under(data, '.updates')
    store.mkdir(exist_ok=True)
    active = store / 'active.json'
    loaded = []
    previous = project
    for path in (active, project / '.update-manifest.json'):
        try:
            manifest = read_manifest(path.read_bytes())
            loaded.append(manifest)
            if path == active:
                code = manifest['program']
                installed = under(store, f'programs/{code["commit"]}')
                if all(matches(under(installed, name), checksum) for name, checksum in code['files'].items()):
                    previous = installed
        except (OSError, ValueError, KeyError, AttributeError, TypeError):
            continue
    try:
        with urlopen(MANIFEST_URL, timeout=6) as response:
            remote = read_manifest(response.read(1024 * 1024 + 1))
        loaded.append(remote)
    except (OSError, HTTPException, ValueError, KeyError, AttributeError, TypeError) as exc:
        log(f'Could not check online updates: {exc}')
    # For the same revision, use the already validated local manifest first.
    revisions = {manifest['revision']: manifest for manifest in reversed(loaded)}
    for revision in sorted(revisions, reverse=True):
        manifest = revisions[revision]
        try:
            program, code_count = prepare_program(project, store, manifest['program'], previous, log)
            asset_count = install_assets(project, data, store, manifest['resources'], log)
            staged = active.with_suffix('.tmp')
            staged.write_text(json.dumps(manifest), encoding='utf-8')
            staged.replace(active)
            log(f'Update revision {revision}: downloaded {code_count} program file(s), '
                f'{asset_count} resource file(s)')
            return program
        except (OSError, HTTPException, ValueError) as exc:
            log(f'Update revision {revision} failed: {exc}')
    return previous


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project', type=Path)
    parser.add_argument('data', type=Path)
    parser.add_argument('log', type=Path)
    args = parser.parse_args()

    def log(message):
        with args.log.open('a', encoding='utf-8') as output:
            output.write(f'[{datetime.now().isoformat(timespec="seconds")}] {message}\n')

    print(json.dumps(str(startup(args.project.resolve(), args.data.resolve(), log))))


if __name__ == '__main__':
    main()
