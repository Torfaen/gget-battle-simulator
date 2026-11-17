"""Extract original stage assets and the MessagePack master tables."""

import argparse
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re
import shutil
import struct
import sys
from tempfile import NamedTemporaryFile
from urllib.parse import urlsplit
from urllib.request import urlopen
from zipfile import ZipFile

from prepare_android import ANDROID, ARTIFACTS, digest
from battle_paths import DATA_ROOT, OUTPUTS, RESOURCE_CACHE
from stage_catalog import (EXPANSION_STAGES, INVASION_STAGE_TYPE, MASTER_TABLES, ROAD_STAGE_TYPE, SOURCE, STAGE_UPDATE,
                           TOWER_STAGE_TYPE, UPDATED_TABLES, tower_floors)

STAGE = 90510005
BASE_STAGE_DIR = ANDROID / 'rootfs/game/stage'
UPDATED_MASTER = STAGE_UPDATE / 'master_database.bytes'
UPDATED_OCTO = STAGE_UPDATE / 'OctoCatalog.bytes'
ROSTER_UPDATE = ARTIFACTS / 'roster_update'
sys.path[:0] = [str(ARTIFACTS / 'stage_env'), str(DATA_ROOT / '.resource_runtime')]


@lru_cache(maxsize=2)
def master_with_roster(master):
    """Use a roster update built against this exact prepared runtime master."""
    variants = {BASE_STAGE_DIR / 'master_database.bytes': 'base', UPDATED_MASTER: 'updated'}
    variant = variants.get(master)
    if variant:
        target = ROSTER_UPDATE / f'{variant}.bytes'
        try:
            manifest = json.loads((ROSTER_UPDATE / 'manifest.json').read_bytes())
            expected = manifest['masters'][variant]
            if (manifest['schema'] == 1 and digest(master) == expected['base_sha256'] and
                    digest(target) == expected['sha256']):
                return target
        except (OSError, ValueError, KeyError, TypeError):
            pass
    return master


def stage_octo_catalog(backup, out, support):
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives.padding import PKCS7

    source = (backup / '内部数据/com.bandainamcoent.gget_WW/files/octo/pdb/2001/903646401/octocacheevai')
    raw = source.read_bytes()
    key = (support / 'octo_aes_key.bin').read_bytes()
    if len(key) != 16 or len(raw) < 33 or raw[0] != 1 or (len(raw) - 17) % 16:
        raise ValueError('Unexpected format for the exported Octo database')
    decoder = Cipher(algorithms.AES(key), modes.CBC(raw[1:17])).decryptor()
    unpadder = PKCS7(128).unpadder()
    payload = unpadder.update(decoder.update(raw[17:]) + decoder.finalize()) + unpadder.finalize()
    catalog = out / 'OctoCatalog.bytes'
    catalog.write_bytes(payload)
    return {'source_sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(payload), 'sha256': digest(catalog)}


def indexed_bundle(index, output, fetch):
    from tools.analysis.compare_game_backups import cache_path

    audit = json.loads((RESOURCE_CACHE / 'latest_game_audit.json').read_text('utf-8'))
    candidates = [output] + [cache_path(Path(audit[name]), index) for name in ('raw_export', 'baseline_export')]
    for path in candidates:
        if path.is_file() and path.stat().st_size == index['size']:
            contents = path.read_bytes()
            if hashlib.md5(contents).hexdigest() == index['md5']:
                if path != output:
                    output.write_bytes(contents)
                return contents
    if fetch:
        object_name = bytes.fromhex(index['object_name_hex']).decode('ascii')
        url = f'https://prd-gl-data.resources.eternal.channel.or.jp/{object_name}'
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or parsed.netloc != 'prd-gl-data.resources.eternal.channel.or.jp':
            raise ValueError('Unexpected Octo resource origin')
        with urlopen(url, timeout=20) as response:
            contents = response.read(index['size'] + 1)
    else:
        return None

    if len(contents) != index['size'] or hashlib.md5(contents).hexdigest() != index['md5']:
        raise ValueError(f"{index['name']} differs from the original resource index")
    output.write_bytes(contents)
    return contents


@lru_cache(maxsize=1)
def resource_index():
    for directory in (STAGE_UPDATE, SOURCE):
        path = directory / 'all_resource_index.json'
        if path.is_file():
            return json.loads(path.read_bytes())
    raise FileNotFoundError('Original battle asset catalog has not been exported')


def stage_bundle(stage, out, fetch, dependencies=True):
    index = stage['stage_bundle']['index']
    stage_id = stage['stage_id']
    object_name = 'Stage_' + index['name'].removeprefix('stage_')
    contents = indexed_bundle(index, out / 'stage.bundle', fetch)
    if contents is None:
        return {'index': index, 'extracted': False}

    import UnityPy
    from tools.extraction.decode_octo_koma import decode_item

    UnityPy.config.FALLBACK_UNITY_VERSION = '2022.3.61f1'
    plain, decoder = decode_item(contents, index)
    environment = UnityPy.load(plain)
    assets = []
    stage_objects = []
    for obj in environment.objects:
        name = getattr(obj.read(), 'm_Name', '')
        assets.append({'type': obj.type.name, 'path_id': obj.path_id, 'name': name})
        if obj.type.name == 'MonoBehaviour' and name == object_name:
            stage_objects.append(obj)
    if len(stage_objects) != 1:
        raise ValueError(f'Expected the original {object_name} Unity object')
    typed = stage_objects[0].read_typetree()
    entity = typed['_stageEntity']
    if entity['_stageId'] != stage_id or len(entity['_maps']) != 1:
        raise ValueError('Unexpected stage ID or map count in StageData')
    # The original ConvertWithMaster takes the playable NPC list and map from
    # m_map_stage. The serialized asset can still contain an older draft.
    (out / 'stage.unity3d').write_bytes(plain)
    (out / 'StageData.serialized').write_bytes(stage_objects[0].get_raw_data())
    (out / 'StageData.json').write_text(json.dumps(typed, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    entity_path = out / 'StageEntity.json'
    entity_path.write_text(json.dumps(entity, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')

    bundles = []
    for name in entity['AssetBundleNames']:
        if not re.fullmatch(r'[A-Za-z0-9_]+', name):
            raise ValueError(f'Unexpected asset bundle name: {name!r}')
        dependency = resource_index()[name]
        original = indexed_bundle(dependency, out / f'{name}.bundle', fetch) if dependencies else None
        info = {'index': dependency, 'extracted': original is not None}
        if original is not None:
            decoded, _ = decode_item(original, dependency)
            UnityPy.load(decoded)
            (out / f'{name}.unity3d').write_bytes(decoded)
            info['unity_sha256'] = hashlib.sha256(decoded).hexdigest()
        bundles.append(info)
    return {'index': index, 'extracted': True, 'sha256': hashlib.sha256(contents).hexdigest(),
            'unity_sha256': hashlib.sha256(plain).hexdigest(), 'decoder': decoder, 'assets': assets,
            'dependency_bundles': bundles, 'entity_sha256': digest(entity_path)}


def pack_memory_database(target, tables):
    import msgpack

    # MasterMemory's header stores offsets relative to the end of the header.
    segments = {}
    names = sorted(tables)
    offset = 0
    for name in names:
        size = len(tables[name])
        segments[name] = [offset, size]
        offset += size
    temporary = target.with_suffix('.tmp')
    with temporary.open('wb') as database:
        database.write(msgpack.packb(segments, use_bin_type=True))
        for name in names:
            database.write(tables[name])
    temporary.replace(target)
    return {'bytes': target.stat().st_size, 'sha256': digest(target)}


def extract_localization(backup, out):
    import UnityPy

    UnityPy.config.FALLBACK_UNITY_VERSION = '2022.3.61f1'
    resources = UnityPy.load((out.parent / 'globalgamemanagers').read_bytes())
    manager = next(obj for obj in resources.objects if obj.type.name == 'ResourceManager')
    container = dict(manager.read_typetree()['m_Container'])

    def read_resource(archive, name):
        pointer = container[name.casefold()]
        external = manager.assets_file.externals[pointer['m_FileID'] - 1].path
        path = 'assets/bin/Data/' + external
        environment = UnityPy.load(archive.read(path))
        asset = next(obj for obj in environment.objects if obj.path_id == pointer['m_PathID'])
        return path, asset

    with ZipFile(backup / 'APK/split_UnityDataAssetPack.apk') as archive:
        config_path, config = read_resource(archive, 'I18nConfig')
        raw = config.get_raw_data()
        # This release stores an empty language array, English as the default, a
        # Resources path, two null asset references, and StringComparison.Ordinal.
        languages, language, length = struct.unpack_from('<III', raw, 44)
        resource = raw[56:56 + length].decode('utf-8')
        pointers = (56 + length + 3) & ~3
        if ((languages, language, resource) != (0, 10, 'English/EnglishLocalization')
                or raw[pointers:] != bytes(24) + struct.pack('<I', 4)):
            raise ValueError('Unexpected localization config for the fixed client build')
        text_path, text = read_resource(archive, resource)
        script = text.read().m_Script
    if not script.startswith('KEY,English\n'):
        raise ValueError('Expected the original English localization CSV')
    csv_path = out / 'EnglishLocalization.csv'
    csv_path.write_bytes(script.encode('utf-8'))
    return {'config_asset': config_path, 'config_sha256': hashlib.sha256(raw).hexdigest(),
            'text_asset': text_path, 'text_sha256': digest(csv_path),
            'language': 'English', 'string_comparison': 4}


def extract_npc_expectations(stage, out, placements, units, characters):
    map_stage_id = stage['map_stage']['Id']
    sides = {row['Id']: row['BattleSideTypeIndex'] for row in placements if row['MapStageId'] == map_stage_id}
    stats = {row['MapNpcId']: (row['Hp'], row['En']) for row in units
             if row['MapNpcId'] in sides and row['Number'] == 0}
    morale = {row['MapNpcId']: row['Mp'] for row in characters
              if row['MapNpcId'] in sides and row['Number'] == 1}
    if set(sides) != set(stats) or set(sides) != set(morale) or len(sides) != len(stage['npcs']):
        raise ValueError('Expected a unique original unit and pilot row for each fixed NPC')
    target = out / 'NpcExpected.tsv'
    with target.open('w', encoding='ascii', newline='\n') as output:
        for npc in sorted(sides):
            hp, en = stats[npc]
            output.write(f'{npc}\t{hp}\t{en}\t{morale[npc]}\t{sides[npc]}\n')
    return {'count': len(sides), 'sha256': digest(target)}


@lru_cache(maxsize=1)
def stage_tables():
    if UPDATED_TABLES.is_file() and UPDATED_MASTER.is_file():
        return json.loads(UPDATED_TABLES.read_bytes())
    base_snapshot = json.loads((BASE_STAGE_DIR / 'stage.json').read_bytes())
    payload = MASTER_TABLES.read_bytes()
    if hashlib.sha256(payload).hexdigest() != base_snapshot['master_sha256']:
        raise ValueError('Parsed master tables differ from the original snapshot')
    tables = json.loads(payload)
    return {name: tables[name] for name in (
        'm_stage', 'm_stage_trial_formation', 'm_map_stage', 'm_map_npc', 'm_map_npc_unit',
        'm_map_npc_character', 'm_map_chip_motif', 'm_map_stage_condition', 'm_map_gimmick_npc', 'm_map', 'm_tower_event_stage',
        'm_invasion_event_stage_group', 'm_invasion_event_stage_extend', 'm_unit', 'm_map_gimmick_npc_unit',
        'm_character', 'm_character_skill', 'm_unit_skill',
    )}


def stage_directory(stage_id):
    if stage_id == STAGE:
        return BASE_STAGE_DIR
    supplied = ROSTER_UPDATE / 'stages' / str(stage_id)
    if (supplied / 'manifest.json').is_file() and master_with_roster(UPDATED_MASTER) != UPDATED_MASTER:
        return supplied
    return ARTIFACTS / 'stages' / str(stage_id)


def stage_prepared(stage_id):
    directory = stage_directory(stage_id)
    return all((directory / name).is_file() for name in ('manifest.json', 'StageEntity.json', 'NpcExpected.tsv'))


def prepared_stage_ids():
    additional = [*(ARTIFACTS / 'stages').glob('*/manifest.json'),
                  *(ROSTER_UPDATE / 'stages').glob('*/manifest.json')]
    ids = {STAGE, *(int(path.parent.name) for path in additional if path.parent.name.isdecimal())}
    return sorted(stage_id for stage_id in ids if stage_prepared(stage_id))


def prepared_npc_ids(tables):
    stages = set(prepared_stage_ids())
    maps = {row['Id'] for row in tables['m_map_stage'] if row['StageId'] in stages}
    return {row['Id'] for row in tables['m_map_npc'] if row['MapStageId'] in maps}


def extract_additional_stage(stage_id, fetch, dependencies=True):
    base_manifest = json.loads((BASE_STAGE_DIR / 'manifest.json').read_text('utf-8'))
    tables = stage_tables()
    stage_row = next((row for row in tables['m_stage'] if row['Id'] == stage_id), None)
    if not stage_row or (stage_row['StageTypeIndex'] not in (INVASION_STAGE_TYPE, ROAD_STAGE_TYPE, TOWER_STAGE_TYPE)
                         and stage_id not in EXPANSION_STAGES):
        raise ValueError(f'Unsupported original stage {stage_id}')
    stage_type = stage_row['StageTypeIndex']
    if any(row['StageId'] == stage_id for row in tables['m_stage_trial_formation']):
        raise ValueError(f'{stage_id} requires a trial formation')
    if (stage_row['Group1SortieCount'], stage_row['Group2SortieCount']) != (5, 5):
        raise ValueError(f'{stage_id} requires a different deployment fixture')
    map_stage = next(row for row in tables['m_map_stage'] if row['StageId'] == stage_id)
    map_id = map_stage['MapId']
    npcs = [row for row in tables['m_map_npc'] if row['MapStageId'] == map_stage['Id']]
    gimmicks = [row for row in tables['m_map_gimmick_npc'] if row['MapStageId'] == map_stage['Id']]
    resource = stage_row['ResourceId']
    stage = {'stage_id': stage_id, 'map_stage': map_stage, 'map': {'Id': map_id}, 'npcs': npcs,
             'stage_bundle': {'index': resource_index()[f'stage_{resource}']}}
    out = stage_directory(stage_id)
    out.mkdir(parents=True, exist_ok=True)
    bundle = stage_bundle(stage, out, fetch, dependencies)
    if not bundle['extracted']:
        raise ValueError(f'Stage bundle missing for {stage_id}. Use --fetch-bundle.')

    npc_stats = extract_npc_expectations(stage, out, npcs, tables['m_map_npc_unit'], tables['m_map_npc_character'])
    coordinates = {(int(x), int(z)) for row in tables['m_map_chip_motif'] if row['MapId'] == map_id
                   for x, z in re.findall(r'\((-?\d+),\s*(-?\d+)\)', row['CoordinateList'])}
    report = {'stage_id': stage_id, 'stage_type': stage_type, 'map_id': map_id, 'map_chip_count': len(coordinates),
              'npc_count': len(npcs), 'initial_npcs': sum(row['IsInitiallyPlaced'] for row in npcs),
              'gimmick_count': len(gimmicks), 'initial_gimmicks': sum(not row['IsScriptPlaced'] for row in gimmicks),
              'resource_id': resource, 'stage_bundle': bundle, 'npc_expectations': npc_stats,
              'master_database': base_manifest['master_database'],
              'conditions': [row for row in tables['m_map_stage_condition'] if row['MapStageId'] == map_stage['Id']]}
    if UPDATED_TABLES.is_file() and UPDATED_MASTER.is_file():
        report['master_database'] = {'bytes': UPDATED_MASTER.stat().st_size, 'sha256': digest(UPDATED_MASTER)}
    if stage_type == TOWER_STAGE_TYPE:
        report['tower_event_stage_id'] = tower_floors(tables)[stage_id]['Id']
    elif stage_type == INVASION_STAGE_TYPE:
        group = next((row for row in tables['m_invasion_event_stage_group'] if row['ExtendStageId'] == stage_id), None)
        if group:
            report['invasion_floors'] = {str(row['Level']): {'id': row['Id'], 'buff_rate': row['BuffRate']}
                                         for row in tables['m_invasion_event_stage_extend']
                                         if row['InvasionEventStageGroupId'] == group['Id']}
    with NamedTemporaryFile(dir=out, suffix='.tmp', delete=False) as pending:
        temporary = Path(pending.name)
    try:
        temporary.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
        temporary.replace(out / 'manifest.json')
    finally:
        temporary.unlink(missing_ok=True)
    return report


def main():
    from tools.analysis.compare_game_backups import MasterReader

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', type=int, default=STAGE)
    parser.add_argument('--fetch-bundle', action='store_true', help='fetch the indexed stage bundle if absent locally')
    parser.add_argument('--battle-only', action='store_true', help='skip map presentation assets for additional stages')
    args = parser.parse_args()
    if args.stage != STAGE:
        report = extract_additional_stage(args.stage, args.fetch_bundle, dependencies=not args.battle_only)
        print(json.dumps({key: report[key] for key in ('stage_id', 'map_id', 'npc_count', 'initial_npcs', 'map_chip_count')},
                         indent=2))
        return

    sources = json.loads((ARTIFACTS / 'source/source_manifest.json').read_text('utf-8'))['provenance']
    backup = Path(sources['apk']).parent.parent
    support = RESOURCE_CACHE / 'analysis_support'
    reader = MasterReader(backup, Path(sources['metadata']), (support / 'master_aes_key.bin').read_bytes())
    stage_path = OUTPUTS / 'battle_headless/eternal_road_hard/stage.json'
    stage = json.loads(stage_path.read_text('utf-8'))
    if stage['stage_id'] != STAGE:
        raise ValueError('Expected the fixed Hard stage')
    parsed = MASTER_TABLES
    if digest(parsed) != stage['master_sha256']:
        raise ValueError('Stage snapshot master-table fingerprint differs')

    out = ANDROID / 'rootfs/game/stage'
    tables = out / 'tables'
    tables.mkdir(parents=True, exist_ok=True)
    entries = {}
    for folder in sorted((reader.root / 'tables').iterdir()):
        if not folder.is_dir():
            continue
        files = list(folder.glob('*.bytes'))
        if len(files) != 1:
            raise ValueError(f'Expected one original table for {folder.name}')
        payload = reader.plain(files[0])
        (tables / f'{folder.name}.bytes').write_bytes(payload)
        entries[folder.name] = {'source': reader.provenance[-1], 'bytes': len(payload),
                                'sha256': hashlib.sha256(payload).hexdigest()}
    stage_rows, _ = reader.table('m_stage')
    matching_stage = [row for row in stage_rows if isinstance(row, dict) and row.get('Id') == STAGE]
    trial_rows, _ = reader.table('m_stage_trial_formation')
    if (len(matching_stage) != 1 or matching_stage[0]['StageTypeIndex'] != 8
            or any(row['StageId'] == STAGE for row in trial_rows)):
        raise ValueError('Expected the fixed Eternal Road stage without a trial formation')
    chip_rows, _ = reader.table('m_map_chip_motif')
    chip_coordinates = {
        (int(x), int(z))
        for row in chip_rows if row['MapId'] == stage['map']['Id']
        for x, z in re.findall(r'\((-?\d+),(-?\d+)\)', row['CoordinateList'])
    }
    if chip_coordinates != {(x, z) for x in range(24) for z in range(29)}:
        raise ValueError('Extracted m_map_chip_motif differs from the fixed map coordinates')

    shutil.copyfile(stage_path, out / 'stage.json')
    formation = OUTPUTS / 'battle_headless/account/formation.json'
    shutil.copyfile(formation, out / 'formation.json')
    bundle = stage_bundle(stage, out, args.fetch_bundle)

    report = {
        'stage_id': STAGE, 'map_id': stage['map']['Id'], 'npc_count': len(stage['npcs']),
        'map_chip_count': len(chip_coordinates),
        'table_count': len(entries), 'tables': entries, 'stage_sha256': digest(stage_path),
        'formation_sha256': digest(formation), 'stage_bundle': bundle,
        'master_database': pack_memory_database(out / 'master_database.bytes',
                                               {name: (tables / f'{name}.bytes').read_bytes() for name in entries}),
        'localization': extract_localization(backup, out),
        'npc_expectations': extract_npc_expectations(stage, out, *(reader.table(name)[0] for name in
                                                    ('m_map_npc', 'm_map_npc_unit', 'm_map_npc_character'))),
        'octo_catalog': stage_octo_catalog(backup, out, support),
    }
    (out / 'manifest.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({key: value for key, value in report.items() if key != 'tables'}, indent=2))


if __name__ == '__main__':
    main()
