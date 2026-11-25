"""Check selectable and prepared-stage artwork against the published runtime tables."""

import argparse
from contextlib import ExitStack
from io import BytesIO
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from zipfile import ZipFile

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'original_battle_reuse')]

from formation import catalog_for
from original_battle_reuse.koma_assets import DIRECTIONS, FRONT, directional_koma_ids


ART = 'original_battle_reuse/artifacts/'
BASE = ART + 'android/rootfs/game/stage/'
UPDATED = ART + 'stage_update/'
IDLE_INDEX = 'idle_koma/unit_index.json'


def collect_npcs(value, mounted):
    if isinstance(value, list):
        for child in value:
            yield from collect_npcs(child, mounted)
    elif isinstance(value, dict):
        unit = value.get('_unitEntity')
        if isinstance(unit, dict) and not value.get('_isAdvOnly'):
            yield from (unit[key] for key in ('_masterId', '_sfsUnitId') if unit.get(key))
            if unit.get('_sfsUnitId'):
                mounted.update((unit['_masterId'], unit['_sfsUnitId']))
        for child in value.values():
            if isinstance(child, (dict, list)):
                yield from collect_npcs(child, mounted)


def release_requirements(runtime, directory, mounted_resources):
    catalogs = [catalog_for(Path(runtime.extract(root + 'master_database.bytes', directory)))
                for root in (BASE, UPDATED)]
    base, current = catalogs
    source = json.loads(runtime.read(UPDATED + 'all_resource_index.json'))
    stages = [name for name in runtime.namelist() if name.endswith('/StageEntity.json')]
    if not stages:
        raise ValueError('No prepared stage data in the release runtime')
    keys = {identifier for catalog in catalogs for identifier in catalog.playable('unit')}
    mounted = set()
    for stage in stages:
        keys.update(collect_npcs(json.loads(runtime.read(stage)), mounted))
    directional = directional_koma_ids(((row[0], row[4], row[19], row[23]) for catalog in catalogs
                                        for row in catalog.units.values()), mounted) | mounted_resources
    sprites = {}
    expected = {'pilot_portraits': set(), 'supporter_portraits': set(), 'idle_koma': set()}
    no_source = {kind: set() for kind in expected}
    for identifier in sorted(keys):
        resource = current.mecha_koma(identifier) or base.mecha_koma(identifier)
        if resource and resource + '_standby' in source:
            sprites[str(identifier)] = resource
            directions = DIRECTIONS if resource in directional else FRONT
            expected['idle_koma'].update(f'idle_koma/{resource}/{direction}.png' for direction in directions)
        else:
            no_source['idle_koma'].add((identifier, resource))
    for catalog in catalogs:
        for kind, row_index, prefix, table in (('pilot', 2, 'cb_', catalog.pilots),
                                             ('supporter', 7, 'sb_', catalog.supporters)):
            folder = kind + '_portraits'
            for identifier in catalog.playable(kind):
                resource = table[identifier][row_index]
                if resource and prefix + resource in source:
                    expected[folder].add(f'battle_ui_assets/{folder}/{resource}.png')
                else:
                    no_source[folder].add((identifier, resource or ''))
    return expected, sprites, no_source, directional


def check_release(runtime_path, archive_paths, art_dir=None, *, write_index=False):
    with ExitStack() as stack:
        runtime = stack.enter_context(ZipFile(runtime_path))
        directory = stack.enter_context(TemporaryDirectory(prefix='art-catalog-'))
        archives = {}
        for archive_path in archive_paths:
            archive = stack.enter_context(ZipFile(archive_path))
            for name in archive.namelist():
                archives[name.removeprefix(ART)] = (archive, name)

        def read(name):
            if art_dir and (path := art_dir / name).is_file():
                return path.read_bytes()
            archive, member = archives[name]
            return archive.read(member)

        try:
            mounted = set(json.loads(read('idle_koma/sfs_geometry.json')))
        except KeyError:
            mounted = set()
        expected, sprites, no_source, directional = release_requirements(runtime, directory, mounted)
        try:
            index = json.loads(read(IDLE_INDEX))
        except KeyError:
            index = {}
        if write_index:
            index.update(sprites)
            target = art_dir / IDLE_INDEX
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(index, indent=2) + '\n', encoding='utf-8')
        missing = {}
        for kind, paths in expected.items():
            failures = []
            for name in sorted(paths):
                try:
                    with Image.open(BytesIO(read(name))) as image:
                        if image.format != 'PNG' or 'A' not in image.getbands() or not image.getchannel('A').getbbox():
                            failures.append(name)
                except (KeyError, OSError, ValueError):
                    failures.append(name)
            missing[kind] = failures
        bad_index = [key for key, resource in sprites.items() if index.get(key) != resource]
        return {'expected': {kind: len(paths) for kind, paths in expected.items()},
                'missing_or_invalid': missing, 'incorrect_index': bad_index,
                'directional_koma': sorted(directional),
                'no_original_index_entry': {kind: sorted(entries) for kind, entries in no_source.items()}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--archive', type=Path, action='append', required=True,
                        help='base art and existing overlays, in install order')
    parser.add_argument('--art-dir', type=Path)
    parser.add_argument('--write-index', action='store_true')
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    if args.write_index and not args.art_dir:
        parser.error('--write-index requires --art-dir')
    report = check_release(args.runtime, args.archive, args.art_dir, write_index=args.write_index)
    if args.report:
        args.report.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    for kind, count in report['expected'].items():
        missing = len(report['missing_or_invalid'][kind])
        no_source = len(report['no_original_index_entry'][kind])
        print(f'{kind}: {count - missing}/{count} verified files; {no_source} IDs without an original index entry')
    print(f'Incorrect or missing idle index mappings: {len(report["incorrect_index"])}')
    if any(report['missing_or_invalid'].values()) or report['incorrect_index']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
