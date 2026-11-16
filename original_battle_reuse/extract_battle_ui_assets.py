"""Extract the original pilot thumbnails and battle map sprites from local backups."""

import argparse
import json
from pathlib import Path
import re
import subprocess
from tempfile import TemporaryDirectory
from zipfile import ZipFile

from extract_idle_koma import ARTIFACTS, BASE_STAGE_DIR, SOURCE, decode_item, read_json
from battle_paths import RESOURCE_CACHE
from prepare_stage import indexed_bundle, prepared_npc_ids, resource_index, stage_tables
from stage_catalog import UPDATED_LANGUAGES

import UnityPy


MAP_UI_SPRITES = {
    'UI_Common_TypeIcon_Attack_S_Map': 'roles/1.png',
    'UI_Common_TypeIcon_Defense_S_Map': 'roles/2.png',
    'UI_Common_TypeIcon_Support_S_Map': 'roles/3.png',
    # The KomaLabelForAttackPrediction prefab uses these three hit icons.
    'UI_Battle_MapUI_Icon_HitOwn': 'forecast/own.png',
    'UI_Battle_MapUI_Icon_HitEnemy': 'forecast/enemy.png',
    'UI_Battle_MapUI_Icon_Hit3rd': 'forecast/other.png',
    'ui_battle_cursor_attack': 'forecast/target.png',
    'ui_area_marker_green': 'map_markers/defense.png',
    'ui_area_marker_blue': 'map_markers/destination.png',
    'UI_Battle_MapUI_MoveAlert': 'map_markers/danger_normal.png',
    'UI_Common_Icon_AssistDeffence_S': 'map_markers/support_defense.png',
    'UI_Battle_MapUI_Icon_MapWeapon_WideArea': 'map_markers/danger_map.png',
    'UI_Battle_MapUI_HPRecovery_TargetAreaBase': 'map_markers/aura_1.png',
    'UI_Battle_MapUI_ENRecovery_TargetAreaBase': 'map_markers/aura_2.png',
    'UI_Battle_MapUI_DamageRise_TargetAreaBase': 'map_markers/aura_3.png',
    'UI_Battle_MapUI_DamageDecrease_TargetAreaBase': 'map_markers/aura_4.png',
    'UI_Battle_MapUI_OtherDebuff_TargetAreaBase': 'map_markers/aura_6.png',
}
# The battle scene's MoveGimmickWeaponMarker SpriteRenderer sets this alpha.
MAP_WEAPON_WARNING_ALPHA = 89
UI_LANGUAGE_PREFIXES = {
    'm_unit': ('110100',),
    'm_character': ('100100',),
    'm_weapon': ('120100',),
    'm_weapon_trait': ('140800',),
    'm_unit_skill': ('141300', '141400'),
}
FORMATION_LANGUAGE_TABLES = ('m_option_parts', 'm_supporter', 'm_series', 'm_lineage', 'm_trait',
                             'm_trait_set_detail', 'm_supporter_active_skill', 'm_supporter_leader_skill_content')


def export_formation_names(out, live_mumu):
    from tools.analysis.compare_game_backups import MasterReader

    reader = MasterReader.__new__(MasterReader)
    reader.key = (RESOURCE_CACHE / 'analysis_support/master_aes_key.bin').read_bytes()
    reader.provenance = []
    names = {}
    with TemporaryDirectory(dir=out) as scratch:
        if live_mumu:
            from android_bridge import adb_shell, find_mumu

            adb = find_mumu()
            remote = '/sdcard/Android/data/com.bandainamcoent.gget_WW/files/master_data/language_tables'
            reader.root = Path(scratch)
        else:
            audit = read_json(RESOURCE_CACHE / 'latest_game_audit.json')
            reader.root = Path(audit['raw_export']) / '外部数据/com.bandainamcoent.gget_WW/files/master_data'
        for table in FORMATION_LANGUAGE_TABLES:
            if live_mumu:
                directory = f'{remote}/{table}'
                filename = adb_shell(adb, f'ls {directory}')
                if not re.fullmatch(r'[A-Z2-7]{52}\.json', filename):
                    raise ValueError(f'Unexpected original language file for {table}: {filename}')
                source = reader.root / filename
                subprocess.run([*adb, 'pull', f'{directory}/{filename}', str(source)], check=True)
            else:
                sources = list((reader.root / 'language_tables' / table).glob('*.json'))
                if len(sources) != 1:
                    raise ValueError(f'Expected one original language file for {table}')
                source = sources[0]
            rows = json.loads(reader.plain(source))['data']
            names[table] = {str(row['id']): row['value'] for row in rows}
    (out / 'formation_names.json').write_text(json.dumps(names, ensure_ascii=False), encoding='utf-8')


def export_map_ui(apk, out):
    bundle = 'assets/bin/Data/sharedassets4.assets'
    with ZipFile(apk) as archive:
        parts = [name for name in archive.namelist() if name == bundle or name.startswith(bundle + '.split')]
        parts.sort(key=lambda name: int(name.removeprefix(bundle + '.split')) if name != bundle else 0)
        export_map_sprites(b''.join(archive.read(name) for name in parts), out)


def export_map_sprites(data, out):
    found = set()
    for obj in UnityPy.load(data).objects:
        if obj.type.name == 'Sprite' and (name := obj.peek_name()) in MAP_UI_SPRITES:
            target = out / MAP_UI_SPRITES[name]
            target.parent.mkdir(parents=True, exist_ok=True)
            image = obj.read().image
            if name == 'UI_Battle_MapUI_Icon_MapWeapon_WideArea':
                image = image.convert('RGBA')
                image.putalpha(image.getchannel('A').point(lambda alpha: alpha * MAP_WEAPON_WARNING_ALPHA // 255))
            image.save(target)
            found.add(name)
    if found != MAP_UI_SPRITES.keys():
        raise ValueError(f'Original battle map sprites missing from the pinned APK: {MAP_UI_SPRITES.keys() - found}')


def export_pilots(index, out, characters, fetch):
    missing = []
    bundles = out.parent / 'pilot_bundles'
    bundles.mkdir(exist_ok=True)
    for row in characters:
        target = out / f'{row["Id"]}.png'
        if target.is_file():
            continue
        name = 'thum_' + row['ResourceId']
        item = index.get(name)
        data = indexed_bundle(item, bundles / f'{name}.bundle', fetch) if item else None
        if data is None:
            missing.append(row['Id'])
            continue
        plain, _ = decode_item(data, item)
        sprites = [obj for obj in UnityPy.load(plain).objects if obj.type.name == 'Sprite' and obj.peek_name() == name]
        if len(sprites) != 1:
            raise ValueError(f'Expected one original pilot sprite in {name}')
        sprites[0].read().image.save(target)
    return missing


def _export_formation_sprite(name, target, bundle, size, fetch):
    if not re.fullmatch(r'[A-Za-z0-9_]+', name):
        return None
    if target.is_file():
        return target
    item = resource_index().get(name)
    if not item:
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=target.parent) as scratch:
        scratch = Path(scratch)
        if not bundle.is_file():
            bundle = scratch / f'{name}.bundle'
        data = indexed_bundle(item, bundle, fetch)
        if data is None:
            return None
        UnityPy.config.FALLBACK_UNITY_VERSION = '2022.3.61f1'
        contents, _ = decode_item(data, item)
        for obj in UnityPy.load(contents).objects:
            if obj.type.name == 'Sprite' and obj.peek_name() == name:
                image = obj.read().image.convert('RGBA')
                image.thumbnail((size, size))
                pending = scratch / target.name
                image.save(pending)
                pending.replace(target)
                return target
    return None


def export_pilot_portrait(resource, out, fetch=True):
    name = 'cb_' + resource
    return _export_formation_sprite(name, out / 'pilot_portraits' / f'{resource}.png',
                                    out / 'pilot_bundles' / f'{name}.bundle', 540, fetch)


def export_supporter_portrait(resource, out, fetch=True):
    name = 'sb_' + resource
    return _export_formation_sprite(name, out / 'supporter_portraits' / f'{resource}.png',
                                    out / 'supporter_bundles' / f'{name}.bundle', 540, fetch)


def export_pilot_icon(resource, out, fetch=True):
    return _export_formation_sprite(resource, out / 'traits' / f'{resource}.png',
                                    out / 'trait_bundles' / f'{resource}.bundle', 96, fetch)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fetch-bundles', action='store_true', help='download missing original pilot thumbnails')
    parser.add_argument('--map-assets', type=Path, help='export only map sprites from an unpacked sharedassets4.assets')
    parser.add_argument('--formation-names', choices=('backup', 'mumu'),
                        help='export only original localized supporter, OP and series names')
    args = parser.parse_args()
    UnityPy.config.FALLBACK_UNITY_VERSION = '2022.3.61f1'
    out = ARTIFACTS / 'battle_ui_assets'
    if args.formation_names:
        out.mkdir(parents=True, exist_ok=True)
        export_formation_names(out, live_mumu=args.formation_names == 'mumu')
        return
    if args.map_assets:
        export_map_sprites(args.map_assets.read_bytes(), out)
        return
    for folder in ('pilots', 'roles'):
        (out / folder).mkdir(parents=True, exist_ok=True)
    if not all((out / relative).is_file() for relative in MAP_UI_SPRITES.values()):
        audit = read_json(RESOURCE_CACHE / 'latest_game_audit.json')
        export_map_ui(Path(audit['raw_export']) / 'APK/split_UnityDataAssetPack.apk', out)
    tables = stage_tables()
    languages = read_json(UPDATED_LANGUAGES if UPDATED_LANGUAGES.is_file() else SOURCE / 'languages.json')
    characters = tables['m_character']
    roles = {row['Id']: row['RoleTypeIndex'] for row in characters}
    (out / 'character_roles.json').write_text(json.dumps(roles, separators=(',', ':')) + '\n', encoding='utf-8')
    skill_names_path = out / 'skill_names.json'
    skill_names = read_json(skill_names_path) if skill_names_path.is_file() else {}
    for table in ('m_character_skill', 'm_unit_skill'):
        entries = languages.get(table, {})
        skill_names.setdefault(table, {}).update({str(row['Id']): entries[str(row['NameLanguageId'])]
                                                  for row in tables[table] if str(row['NameLanguageId']) in entries})
    (out / 'skill_names.json').write_text(json.dumps(skill_names, ensure_ascii=False) + '\n', encoding='utf-8')
    names_path = out / 'master_names.json'
    names = read_json(names_path) if names_path.is_file() else {}
    for table, prefixes in UI_LANGUAGE_PREFIXES.items():
        entries = names.get(table, {}) | languages.get(table, {})
        names[table] = {key: text for key, text in entries.items() if key.startswith(prefixes)}
    names_path.write_text(json.dumps(names, ensure_ascii=False), encoding='utf-8')
    pilot_ids = {int(line.split()[-1]) for line in (BASE_STAGE_DIR / 'PlayerExpected.tsv').read_text().splitlines()}
    npcs = prepared_npc_ids(tables)
    pilot_ids.update(row['CharacterId'] for row in tables['m_map_npc_character'] if row['MapNpcId'] in npcs)
    missing = export_pilots(resource_index(), out / 'pilots',
                            [row for row in characters if row['Id'] in pilot_ids and row['ResourceId']], args.fetch_bundles)
    print(f'Prepared original role and forecast sprites and {len(list((out / "pilots").glob("*.png")))} pilot thumbnails.')
    if missing:
        print(f'Original pilot thumbnails unavailable: {missing}')


if __name__ == '__main__':
    main()
