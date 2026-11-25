"""Export original map-unit idle sprites from the local resource backups."""

import argparse
import hashlib
import json
from pathlib import Path

from prepare_stage import (ARTIFACTS, BASE_STAGE_DIR, SOURCE, indexed_bundle, prepared_npc_ids,
                           prepared_stage_ids, resource_index, stage_tables)
from stage_catalog import UPDATED_LANGUAGES, UPDATED_TABLES
from prepare_demo_formation import DEMO_UNITS

import UnityPy
from UnityPy.enums import SpritePackingMode
from UnityPy.export.SpriteHelper import MeshHandler, SpriteSettings

from battle_paths import RESOURCE_CACHE
from tools.analysis.compare_game_backups import cache_path
from tools.extraction.decode_octo_koma import decode_item
from extract_battle_map import transform_point
from original_battle_reuse.koma_assets import DIRECTIONS, FRONT, directional_koma_ids


IDLE_DIR = ARTIFACTS / 'idle_koma'


def read_json(path):
    return json.loads(path.read_bytes())


def opening_roster(log):
    stage_id = None
    units = []
    with log.open(encoding='utf-8', errors='replace') as records:
        for record in records:
            fields = record.rstrip('\r\n').split('\t')
            if fields[:2] == ['UI', 'STATE']:
                stage_id = int(fields[2])
                units = []
            elif stage_id is not None and fields[:2] == ['UI', 'UNIT']:
                units.append({'number': int(fields[2]), 'side': int(fields[3]), 'master_id': int(fields[11])})
            elif stage_id is not None and fields[:2] == ['UI', 'READY'] and units:
                return stage_id, units
    raise ValueError(f'No complete opening roster in {log}')


def renderer_transforms(renderer):
    game_object = renderer.m_GameObject.read()
    transform = next(component.component.read() for component in game_object.m_Component
                     if component.component.deref().type.name == 'Transform')
    chain = [transform]
    while transform.m_Father.path_id:
        transform = transform.m_Father.read()
        chain.append(transform)
    return chain


def sprite_corners(sprite, renderer, chain, size):
    # UnityPy renders tight meshes into their own bounds, which can trim the source rect.
    ppu = sprite.m_PixelsToUnits
    left = -sprite.m_Rect.width * sprite.m_Pivot.x / ppu
    bottom = -sprite.m_Rect.height * sprite.m_Pivot.y / ppu
    settings = SpriteSettings(sprite.m_RD.settingsRaw)
    if settings.packingMode == SpritePackingMode.kSPMTight:
        mesh = MeshHandler(sprite.m_RD, sprite.object_reader.version)
        mesh.process()
        if mesh.m_UV0 and any(u or v for u, v in mesh.m_UV0):
            left = min(x for x, _, _ in mesh.m_Vertices)
            bottom = min(y for _, y, _ in mesh.m_Vertices)
    right = left + size[0] / ppu
    top = bottom + size[1] / ppu

    def to_root(x, y):
        point = (-x if renderer.m_FlipX else x, -y if renderer.m_FlipY else y, 0)
        for node in chain[:-1]:
            point = transform_point(point, node.m_LocalPosition, node.m_LocalRotation, node.m_LocalScale)
        return [round(component, 7) for component in point]

    return [to_root(left, top), to_root(right, top), to_root(left, bottom)]


def read_idle_sprites(index, caches, downloads=None, *, fetch=False, directions=DIRECTIONS):
    name = index['name']
    paths = (cache_path(cache, index) for cache in caches)
    source = next((path for path in paths if path.is_file()), None)
    if source is not None:
        contents = source.read_bytes()
    else:
        contents = indexed_bundle(index, downloads / f'{name}.bundle', fetch=fetch) if downloads else None
    if contents is None:
        raise FileNotFoundError(f'Original idle bundle is absent from the local backups: {name}')
    if len(contents) != index['size'] or hashlib.md5(contents).hexdigest() != index['md5']:
        raise ValueError(f'{name} differs from the original resource index')

    plain, _ = decode_item(contents, index)
    expected = {(name, 'standby', 'standby_ctrl', f'standby_{direction}', 'base'): direction
                for direction in directions}
    sprites = {}
    for obj in UnityPy.load(plain).objects:
        if obj.type.name != 'SpriteRenderer':
            continue
        renderer = obj.read()
        chain = renderer_transforms(renderer)
        path = tuple(node.m_GameObject.read().m_Name for node in reversed(chain))
        direction = expected.get(path)
        if direction is None:
            continue
        pointer = renderer.m_Sprite
        if pointer.file_id != 0 or not pointer.path_id or direction in sprites:
            raise ValueError(f'Invalid standby SpriteRenderer for {name}/{direction}')
        sprite = pointer.read()
        sprites[direction] = (sprite, str(pointer.path_id), renderer, chain)
    if sprites.keys() != set(directions):
        raise ValueError(f'Missing standby sprites for {name}: {sorted(set(directions) - sprites.keys())}')
    return sprites


def load_sources(tables=None):
    UnityPy.config.FALLBACK_UNITY_VERSION = '2022.3.61f1'
    units = (stage_tables() if tables is None else tables)['m_unit']
    master = {row['Id']: row for row in units}
    audit = read_json(RESOURCE_CACHE / 'latest_game_audit.json')
    caches = [Path(audit[key]) for key in ('raw_export', 'baseline_export')]
    return master, resource_index(), caches


def export_frames(out, koma_id, bundle, caches, directions, downloads=None, *, fetch=False, geometry=False):
    sprites = read_idle_sprites(bundle, caches, downloads, fetch=fetch, directions=directions)
    (out / koma_id).mkdir(parents=True, exist_ok=True)
    frames = {}
    for direction in directions:
        sprite, path_id, renderer, chain = sprites[direction]
        image = sprite.image.convert('RGBA')
        if image.getchannel('A').getbbox() is None:
            raise ValueError(f'Empty original sprite: {sprite.m_Name}')
        path = out / koma_id / f'{direction}.png'
        image.save(path)
        frames[direction] = {
            'file': path.relative_to(out).as_posix(),
            'sprite': sprite.m_Name,
            'path_id': path_id,
            'size': image.size,
            'unity_pivot': [sprite.m_Pivot.x, sprite.m_Pivot.y],
        }
        if geometry:
            frames[direction]['corners'] = sprite_corners(sprite, renderer, chain, image.size)
    return frames


def prepared_player_ids():
    import msgpack
    from prepare_player import TABLE_NAMES, table_schemas

    player_database = BASE_STAGE_DIR / 'PlayerDatabase.bytes'
    if not player_database.is_file():
        return set()
    fields = [parameter['name'] for parameter in table_schemas()['UserUnit']]
    with player_database.open('rb') as source:
        header = msgpack.Unpacker(source, raw=False)
        offset, size = next(header)[TABLE_NAMES['UserUnit']]
        source.seek(header.tell() + offset)
        rows = msgpack.unpackb(source.read(size), raw=False)
    unit_field = fields.index('UnitId')
    return {row[unit_field] for row in rows}


def export_prepared_koma(out, fetch=False):
    import msgpack

    tables = stage_tables()
    master, index, caches = load_sources(tables)
    demo_ids = {unit for unit, _ in DEMO_UNITS}
    unit_ids = demo_ids | prepared_player_ids()
    npcs = prepared_npc_ids(tables)
    mounted_ids = set()
    for row in tables['m_map_npc_unit']:
        if row['MapNpcId'] in npcs:
            unit_ids.update((row['UnitId'], row['SfsUnitId']))
            if row['SfsUnitId']:
                mounted_ids.update((row['UnitId'], row['SfsUnitId']))
    stages = set(prepared_stage_ids())
    maps = {row['Id'] for row in tables['m_map_stage'] if row['StageId'] in stages}
    gimmicks = {row['Id'] for row in tables['m_map_gimmick_npc'] if row['MapStageId'] in maps}
    unit_ids.update(row['UnitId'] for row in tables['m_map_gimmick_npc_unit'] if row['MapGimmickNpcId'] in gimmicks)
    base_units = BASE_STAGE_DIR / 'tables/m_unit.bytes'
    if UPDATED_TABLES.is_file() and base_units.is_file():
        base_ids = {row[0] for row in msgpack.unpackb(base_units.read_bytes(), raw=False)}
        unit_ids.update(master.keys() - base_ids)
    unit_ids.discard(0)
    # Transformations, conversions and escape forms share the original MainUnitId.
    families = {master[key]['MainUnitId'] for key in unit_ids if master[key]['MechanismSetId']}
    unit_ids.update(key for key, row in master.items() if row['MainUnitId'] in families)
    out.mkdir(parents=True, exist_ok=True)
    index_path = out / 'unit_index.json'
    unit_index = read_json(index_path) if index_path.is_file() else {}
    unit_index.update({str(key): master[key]['KomaResourceId'] for key in sorted(unit_ids)
                       if master[key]['KomaResourceId']})
    downloads = out / 'bundles'
    if fetch:
        downloads.mkdir(exist_ok=True)
    mounted_families = {master[key]['MainUnitId'] for key in mounted_ids if master[key]['MechanismSetId']}
    mounted_koma = {unit_index[str(key)] for key in unit_ids if str(key) in unit_index and
                    (key in mounted_ids or master[key]['MainUnitId'] in mounted_families)}
    directional = directional_koma_ids(((row['Id'], row['KomaResourceId'], row['OccupiedAreaId'], row['MainUnitId'])
                                        for row in master.values()), mounted_ids)
    sfs_geometry = {}
    missing = set()
    for koma_id in sorted(set(unit_index.values())):
        mounted = koma_id in mounted_koma
        directions = DIRECTIONS if koma_id in directional else FRONT
        has_frames = all((out / koma_id / f'{direction}.png').is_file() for direction in directions)
        if has_frames and not mounted:
            continue
        try:
            frames = export_frames(out, koma_id, index[f'{koma_id}_standby'], caches, directions, downloads,
                                   fetch=fetch, geometry=mounted)
            if mounted:
                sfs_geometry[koma_id] = {direction: frame['corners'] for direction, frame in frames.items()}
        except (OSError, ValueError, KeyError) as exc:
            if not has_frames:
                missing.add(koma_id)
            print(f'{koma_id}: {exc}', flush=True)
    unit_index = {key: koma_id for key, koma_id in unit_index.items() if koma_id not in missing}
    missing_demo = sorted(key for key in demo_ids if str(key) not in unit_index)
    if missing_demo:
        raise ValueError(f'Missing idle frames for the default formation: {missing_demo}')
    add_idle_aliases(unit_index, master)
    index_path.write_text(json.dumps(unit_index, indent=2) + '\n', encoding='utf-8')
    (out / 'sfs_geometry.json').write_text(json.dumps(sfs_geometry, indent=2) + '\n', encoding='utf-8')
    print(f'Prepared idle frames for {len(unit_index)} master units.')


def add_idle_aliases(unit_index, master):
    available = set(unit_index.values())
    for key, row in master.items():
        if row['KomaResourceId'] in available:
            unit_index.setdefault(str(key), row['KomaResourceId'])


def export_idle_koma(log, out):
    stage_id, units = opening_roster(log)
    master, index, caches = load_sources()
    names = read_json(UPDATED_LANGUAGES if UPDATED_LANGUAGES.is_file() else SOURCE / 'languages.json')['m_unit']

    assets = {}
    for unit in units:
        row = master[unit['master_id']]
        koma_id = row['KomaResourceId']
        unit['koma_id'] = koma_id
        unit['name'] = names.get(str(row['NameLanguageId']))
        if koma_id in assets:
            continue
        bundle = index[f'{koma_id}_standby']
        frames = export_frames(out, koma_id, bundle, caches, DIRECTIONS)
        assets[koma_id] = {'bundle': bundle['name'], 'md5': bundle['md5'], 'frames': frames}
    manifest = {'stage_id': stage_id, 'units': units, 'assets': assets}
    (out / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'Stage {stage_id}: exported {sum(len(asset["frames"]) for asset in assets.values())} idle sprites '
          f'from {len(assets)} original assets for {len(units)} units.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument('log', type=Path, nargs='?', help='interactive.log with the native opening roster')
    inputs.add_argument('--prepared', action='store_true', help='prepare the idle frames for the battle window')
    parser.add_argument('--fetch-bundles', action='store_true', help='download missing idle bundles for prepared stages')
    parser.add_argument('--out', type=Path, default=IDLE_DIR)
    args = parser.parse_args()
    if args.prepared:
        export_prepared_koma(args.out, args.fetch_bundles)
    else:
        export_idle_koma(args.log, args.out)
