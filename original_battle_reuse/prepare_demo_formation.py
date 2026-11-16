"""Generate a local test deployment from public master IDs and synthetic instance IDs."""

from pathlib import Path
from tempfile import TemporaryDirectory

from formation import BASE_MASTER, Force, Member, catalog_for, describe_error, validation_errors
from prepare_stage import BASE_STAGE_DIR, STAGE, pack_memory_database

import msgpack


LOCAL_USER = 1
FORMATION_TYPE = 2
FORMATION_TARGET = 90510001
GROUP_ID = 491542998
DEMO_UNITS = (
    (1001000100, 1001000100), (1001000200, 1001000200), (1001000300, 1001000300),
    (1001000400, 1001000500), (1001000600, 1001000600), (1001000700, 1001000400),
    (1001000800, 1001000700), (1001000900, 1001001400), (1001001100, 1001001700),
    (1001001400, 1001001800),
)
DEMO_SUPPORTERS = (1001000150, 1001000250)
PLAYER_FILES = ('PlayerSelection.tsv', 'PlayerExpected.tsv', 'PlayerDatabase.bytes')


def demo_team(catalog):
    team = []
    for index, supporter in enumerate(DEMO_SUPPORTERS):
        members = [
            Member(unit, pilot, min(60, catalog.unit_max_level(unit, False)),
                   min(60, catalog.pilot_max_level(pilot)), 0, False, False, 1, 0, {})
            for unit, pilot in DEMO_UNITS[5 * index:5 * (index + 1)]
        ]
        team.append(Force(members, supporter, min(60, catalog.supporter_max_level(supporter))))
    if errors := validation_errors(team, catalog, STAGE):
        raise ValueError(f'Demo deployment is invalid: {describe_error(errors[0], team, catalog)}')
    return team


def write_demo_files(directory, catalog, team):
    directory = Path(directory)
    tables = {
        'user_group_formation': [[LOCAL_USER, GROUP_ID, 'Local demo team', True, 0]],
        'user_group_formation_unit': [],
        'user_group_formation_supporter': [],
        'user_unit': [],
        'user_character': [],
        'user_supporter': [],
        'user_weapon': [],
        'user_option_parts': [],
    }
    expected = []
    for force, position, slot_id in catalog.group_slots[GROUP_ID]:
        member = team[force - 1].members[position - 1]
        instance = 1000 + (force - 1) * 5 + position
        form = catalog.sortie_form(member, STAGE)
        tables['user_unit'].append([LOCAL_USER, instance, member.unit, 0, 0, False, False,
                                    catalog.unit_exp[member.level], 0, 0, 0, 0, 0])
        tables['user_character'].append([LOCAL_USER, member.pilot, catalog.pilot_exp[member.pilot_level],
                                         0, False, 0, 0, 0, False, False])
        tables['user_group_formation_unit'].append([LOCAL_USER, slot_id, instance, member.pilot, form])
        for weapon, (_, _, fixed) in catalog.loadout_weapons(member).items():
            if not fixed:
                tables['user_weapon'].append([LOCAL_USER, instance, weapon, 1])
        expected.append((force - 1, position, instance, member.pilot))
    for force, _, slot_id in catalog.group_supporters[GROUP_ID]:
        group = team[force - 1]
        instance = 2000 + force
        tables['user_supporter'].append([LOCAL_USER, instance, group.supporter,
                                         catalog.supporter_exp[group.supporter_level], 0, False, 0])
        tables['user_group_formation_supporter'].append([LOCAL_USER, slot_id, instance])
    if len(expected) != 10 or len(tables['user_supporter']) != 2:
        raise ValueError('The resource pack must have a two-team, ten-unit deployment layout')
    for name, rows in tables.items():
        width = 3 if name == 'user_weapon' else 2
        rows.sort(key=lambda row: row[:width])

    (directory / PLAYER_FILES[0]).write_text(f'{LOCAL_USER}\t{FORMATION_TYPE}\t{FORMATION_TARGET}\n',
                                             encoding='ascii', newline='\n')
    (directory / PLAYER_FILES[1]).write_text(''.join('\t'.join(map(str, row)) + '\n' for row in expected),
                                             encoding='ascii', newline='\n')
    pack_memory_database(directory / PLAYER_FILES[2],
                         {name: msgpack.packb(rows, use_bin_type=True) for name, rows in tables.items()})


def prepare_demo_formation(directory=BASE_STAGE_DIR, master=BASE_MASTER):
    """Preserve an existing deployment and recover matching files from an interrupted setup."""
    if all((directory / name).is_file() for name in PLAYER_FILES):
        return False
    catalog = catalog_for(master)
    with TemporaryDirectory(prefix='demo-deployment-', dir=directory) as scratch:
        staged = Path(scratch)
        write_demo_files(staged, catalog, demo_team(catalog))
        for name in PLAYER_FILES:
            target = directory / name
            if target.exists() and target.read_bytes() != (staged / name).read_bytes():
                raise ValueError(f'Existing deployment is incomplete. Preserve or restore its three player files in {directory}')
        for name in PLAYER_FILES:
            target = directory / name
            if not target.exists():
                (staged / name).rename(target)
    return True


if __name__ == '__main__':
    # This is also the preparation entry point used by the v0.1.2 launcher.
    from update_release_art import update_release_art

    update_release_art()
    created = prepare_demo_formation()
    print('Prepared the local demo deployment.' if created else 'Using the existing local deployment.')
