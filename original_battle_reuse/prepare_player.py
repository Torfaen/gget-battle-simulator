"""Pack the captured sortie formation as original IL2CPP user table inputs."""

from collections import Counter
import hashlib
import json
from pathlib import Path

from prepare_stage import ANDROID, ARTIFACTS, MASTER_TABLES, pack_memory_database
from battle_paths import OUTPUTS
from battle_headless.account import guarded, raw
from tools.analysis.analyze_runtime_metadata import Metadata

LOCAL_USER_ID = 1
TABLE_NAMES = {
    'UserGroupFormation': 'user_group_formation',
    'UserGroupFormationUnit': 'user_group_formation_unit',
    'UserGroupFormationSupporter': 'user_group_formation_supporter',
    'UserUnit': 'user_unit',
    'UserCharacter': 'user_character',
    'UserSupporter': 'user_supporter',
    'UserOptionParts': 'user_option_parts',
    'UserWeapon': 'user_weapon',
}


def field(fields, name):
    return fields[f'<{name}>k__BackingField']


def load_snapshot(directory, name, hashes):
    payload = (directory / name).read_bytes()
    hashes[name] = hashlib.sha256(payload).hexdigest()
    return json.loads(payload)


def index_records(records, kind, key):
    return {raw(field(record['fields'], key)): record['fields']
            for record in records if record['type'] == kind}


def table_schemas():
    source = json.loads((ARTIFACTS / 'source/source_manifest.json').read_bytes())['provenance']
    metadata = Path(source['metadata'])
    if hashlib.sha256(metadata.read_bytes()).hexdigest() != source['metadata_sha256']:
        raise ValueError('User record schemas require the pinned original metadata')
    meta = Metadata(metadata)
    schemas = {}
    for kind in TABLE_NAMES:
        declaration = meta.describe(meta.find_type('Eternal.Infrastructure', kind))
        constructors = [method['parameters'] for method in declaration['methods']
                        if method['name'] == '.ctor' and method['parameters']
                        and method['parameters'][0]['name'] == 'UserId']
        if len(constructors) != 1:
            raise ValueError(f'Expected one serialized record constructor for {kind}')
        schemas[kind] = constructors[0]
    return schemas


def snapshot_record(fields, parameters):
    values = [LOCAL_USER_ID]
    for parameter in parameters[1:]:
        item = field(fields, parameter['name'])
        kind = parameter['type_index']
        if kind == 89364:
            values.append(item.get('text'))
        elif kind == 53286:
            values.append(bool(raw(item, 1, signed=False)))
        else:
            width = {69549: 2, 69569: 4, 69607: 8}[kind]
            values.append(raw(item, width))
    return values


def main():
    import msgpack

    capture_dir = OUTPUTS / 'battle_headless/account'
    hashes = {}
    capture = load_snapshot(capture_dir, 'current_formation_raw.json', hashes)
    roster = load_snapshot(capture_dir, 'live_roster.json', hashes)['records']
    group_records = load_snapshot(capture_dir, 'live_records.json', hashes)['records']
    saved_formation = load_snapshot(capture_dir, 'formation.json', hashes)
    if saved_formation['snapshot_sha256'] != hashes['current_formation_raw.json']:
        raise ValueError('The normalized formation references a different raw capture')
    formation_type = guarded(capture['selection']['GroupFormationType'])
    content_target = guarded(capture['selection']['ContentTargetId'], 64)
    if (formation_type, content_target) != (saved_formation['source_formation_type'],
                                           saved_formation['source_content_target_id']):
        raise ValueError('The formation selection does not match the saved fixture')
    schemas = table_schemas()
    slots = index_records(group_records, 'UserGroupFormationUnit', 'GroupFormationUnitIdxId')
    groups = index_records(group_records, 'UserGroupFormation', 'GroupFormationIdxId')
    pilots = index_records(roster, 'UserCharacter', 'CharacterId')
    supporters = index_records(roster, 'UserSupporter', 'Id')
    tables = json.loads(MASTER_TABLES.read_bytes())
    map_weapons = {row['Id']: row['MainWeaponId'] for row in tables['m_weapon'] if row['MapWeaponRangeTypeIndex']}
    map_by_unit = {}
    for row in tables['m_unit_weapon']:
        if row['WeaponId'] in map_weapons:
            map_by_unit.setdefault(row['UnitId'], set()).add(map_weapons[row['WeaponId']])

    selected = {kind: {} for kind in TABLE_NAMES}
    equipped = Counter()
    own_units = []
    for force in capture['forces']:
        force_id = guarded(force['entity']['GroupFormationIndexId']['fields']['_id'], 64)
        selected['UserGroupFormation'][force_id] = groups[force_id]
        for slot in force['slots']:
            unit = slot['unit']
            pilot = slot['pilot']
            source = unit['raw']
            unit_id = raw(field(source, 'Id'))
            pilot_id = raw(field(pilot['master'], 'Id'))
            slot_id = guarded(field(slot['slot'], 'GroupFormationUnitIndexId')['fields']['_id'], 64)
            position = guarded(slot['slot']['PositionNumber'])
            slot_row = slots[slot_id]
            if (raw(field(slot_row, 'UserUnitId')), raw(field(slot_row, 'CharacterId'))) != (unit_id, pilot_id):
                raise ValueError('Captured slot differs from its original saved record')
            pilot_exp = guarded(field(pilot['entity'], 'TotalExp'), 64)
            if pilot_exp != raw(field(pilots[pilot_id], 'Exp'), 4):
                raise ValueError('Captured pilot experience differs from the original user row')
            selected['UserGroupFormationUnit'][slot_id] = slot_row
            selected['UserUnit'][unit_id] = source
            selected['UserCharacter'][pilot_id] = pilots[pilot_id]
            own_units.append((force['dictionary_key'], position, unit_id, pilot_id))
            option_id = raw(field(source, 'OptionPartsId'))
            if option_id:
                equipped[option_id] += 1
            for item in unit['weapons']:
                weapon_id = guarded(item['Weapon']['fields']['MainWeaponId'], 64)
                level = guarded(field(item, 'WeaponLevel'))
                selected['UserWeapon'][unit_id, weapon_id] = [LOCAL_USER_ID, unit_id, weapon_id, level]
            # The sortie capture lists ordinary weapons. Include master-defined MAP weapons at base level.
            for weapon_id in map_by_unit.get(raw(field(source, 'UnitId')), ()):
                selected['UserWeapon'].setdefault((unit_id, weapon_id), [LOCAL_USER_ID, unit_id, weapon_id, 1])
        for support in force['supporters']:
            supporter_id = guarded(field(support, 'Id')['fields']['_id'], 64)
            if guarded(field(support, 'TotalExp'), 64) != raw(field(supporters[supporter_id], 'Exp'), 4):
                raise ValueError('Captured supporter experience differs from the original user row')
            selected['UserSupporter'][supporter_id] = supporters[supporter_id]

    if len(own_units) != 10 or len(selected['UserUnit']) != 10 or len(selected['UserSupporter']) != 2:
        raise ValueError('Expected the captured ten-unit, two-supporter formation')
    supporter_rows = index_records(group_records, 'UserGroupFormationSupporter', 'GroupFormationSupporterIdxId')
    selected['UserGroupFormationSupporter'] = {
        key: row for key, row in supporter_rows.items()
        if raw(field(row, 'UserSupporterId')) in selected['UserSupporter']
    }
    # The local inventory records represent the instances equipped in this fixture.
    selected['UserOptionParts'] = {part: [LOCAL_USER_ID, part, count, 0] for part, count in equipped.items()}
    payloads = {}
    for kind, name in TABLE_NAMES.items():
        rows = selected[kind]
        records = [row if isinstance(row, list) else snapshot_record(row, schemas[kind])
                   for key in sorted(rows) for row in (rows[key],)]
        if any(len(row) != len(schemas[kind]) for row in records):
            raise ValueError(f'Unexpected serialized row length for {kind}')
        payloads[name] = msgpack.packb(records, use_bin_type=True)
    stage = ANDROID / 'rootfs/game/stage'
    database = pack_memory_database(stage / 'PlayerDatabase.bytes', payloads)
    (stage / 'PlayerSelection.tsv').write_text(f'{LOCAL_USER_ID}\t{formation_type}\t{content_target}\n',
                                               encoding='ascii', newline='\n')
    with (stage / 'PlayerExpected.tsv').open('w', encoding='ascii', newline='\n') as output:
        for row in sorted(own_units):
            output.write('\t'.join(map(str, row)) + '\n')
    report = {'source_sha256': hashes, 'database': database,
              'counts': {TABLE_NAMES[kind]: len(rows) for kind, rows in selected.items()},
              'player_unit_count': len(own_units), 'supporter_count': len(selected['UserSupporter']),
              'formation_type': formation_type, 'content_target': content_target}
    (stage / 'player_manifest.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
