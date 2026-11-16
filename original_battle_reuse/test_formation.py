from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from prepare_stage import ARTIFACTS, BASE_STAGE_DIR

if not all((BASE_STAGE_DIR / name).is_file() for name in ('master_database.bytes', 'PlayerDatabase.bytes')):
    raise unittest.SkipTest('Requires the original local deployment fixtures')

from formation import (BASE_MASTER, catalog_for, default_team, load_sortie, load_team,
                       master_for_stage, packed_tables, save_team, team_preview, validation_errors, write_player_inputs)
from prepare_demo_formation import prepare_demo_formation
from stage_catalog import StageChoice


class FormationTests(unittest.TestCase):
    def setUp(self):
        self.air = 90700118
        self.space = 90700101
        self.master = master_for_stage(self.air)
        self.catalog = catalog_for(self.master)
        self.team = default_team(self.catalog)

    def test_air_uses_transformation_and_preserves_base_unit(self):
        import msgpack

        member = self.team[1].members[2]
        member.unit, member.sp, member.ssp, member.op, member.weapons = 1080000100, False, False, 0, {}
        member.level = min(member.level, self.catalog.unit_max_level(member.unit, False))
        member.ssp_cores = None
        self.assertEqual(self.catalog.sortie_form(member, self.air), 1080000200)
        self.assertEqual(self.catalog.sortie_form(member, self.space), member.unit)
        for force in self.team:
            for slot in force.members:
                if self.catalog.sortie_form(slot, self.air) is None:
                    slot.unit, slot.level, slot.op, slot.weapons = 1055000160, 90, 0, {}
        member.weapons[108000020001] = 4
        with TemporaryDirectory(dir=ARTIFACTS) as scratch:
            directory = Path(scratch) / 'formation'
            write_player_inputs(directory, self.team, self.air, self.master)
            db = {key: msgpack.unpackb(data, raw=False)
                  for key, data in packed_tables(directory / 'PlayerDatabase.bytes')}
            record = next(row for row in db['user_group_formation_unit'] if row[4] == 1080000200)
            self.assertTrue(any(row[1:3] == [record[2], 1080000100] for row in db['user_unit']))
            self.assertIn([1, record[2], 108000020001, 4], db['user_weapon'])

    def test_space_rechecks_op_terrain(self):
        member = self.team[0].members[0]
        member.unit, member.sp, member.ssp, member.op, member.weapons = 1001001200, False, False, 0, {}
        member.level = min(70, self.catalog.unit_max_level(member.unit, False))
        member.ssp_cores = None
        self.assertIsNone(self.catalog.sortie_form(member, self.space))
        self.assertTrue(any(error[:2] == (1, 1) for error in validation_errors(self.team, self.catalog, self.space)))
        member.op = 400084
        self.assertEqual(self.catalog.sortie_form(member, self.space), member.unit)
        self.assertFalse(any(error[:2] == (1, 1) for error in validation_errors(self.team, self.catalog, self.space)))

    def test_demo_deployment_uses_local_instances_and_preserves_existing_files(self):
        import msgpack

        with TemporaryDirectory(dir=ARTIFACTS) as scratch:
            directory = Path(scratch)
            self.assertTrue(prepare_demo_formation(directory, BASE_MASTER))
            files = {entry.name: entry.read_bytes() for entry in directory.iterdir()}
            database = {name: msgpack.unpackb(payload, raw=False)
                        for name, payload in packed_tables(directory / 'PlayerDatabase.bytes')}
            self.assertEqual([row[1] for row in database['user_unit']], list(range(1001, 1011)))
            self.assertEqual([row[1] for row in database['user_supporter']], [2001, 2002])
            self.assertFalse(prepare_demo_formation(directory, BASE_MASTER))
            self.assertEqual({entry.name: entry.read_bytes() for entry in directory.iterdir()}, files)

    def test_team_round_trip_and_invalid_format(self):
        with TemporaryDirectory(dir=ARTIFACTS) as scratch:
            path = Path(scratch) / 'team.ggetteam'
            save_team(path, self.team)
            with path.open('rb') as source:
                self.assertEqual(load_team(source), self.team)
        with self.assertRaises(ValueError):
            load_team(BytesIO(b'{"version": 1, "forces": []}'))

    def test_sortie_round_trip_with_floor_and_legacy_team(self):
        with TemporaryDirectory(dir=ARTIFACTS) as scratch:
            path = Path(scratch) / 'last_formation.ggetteam'
            save_team(path, self.team)
            with path.open('rb') as source:
                self.assertEqual(load_sortie(source), (self.team, None))

            stage = StageChoice('17F', 90230001, 17)
            save_team(path, self.team, stage=stage)
            with path.open('rb') as source:
                self.assertEqual(load_sortie(source), (self.team, (90230001, 17)))
            with path.open('rb') as source:
                self.assertEqual(load_team(source), self.team)

    def test_team_preview_is_optional_and_does_not_change_the_team(self):
        with TemporaryDirectory(dir=ARTIFACTS) as scratch:
            path = Path(scratch) / 'team.ggetteam'
            save_team(path, self.team)
            self.assertIsNone(team_preview(path)[0])
            save_team(path, self.team, preview=b'preview')
            self.assertEqual(team_preview(path)[0], b'preview')
            with path.open('rb') as source:
                self.assertEqual(load_team(source), self.team)

    def test_pilot_numbers_include_unconditional_abilities(self):
        values = {name: (value, base) for name, value, base in self.catalog.pilot_numbers(1110000100, 100)}
        self.assertEqual(values['射击'], (629, 629))
        self.assertEqual(values['反应'], (688, 574))
        self.assertEqual(values['觉醒'], (841, 673))
        self.assertEqual(values['战斗力'], (3903, 3903))
        low = {name: (value, base) for name, value, base in self.catalog.pilot_numbers(1110000100, 1)}
        self.assertEqual(low['反应'], (120, 120))

    def test_supporter_values_and_condition_connectors(self):
        self.assertEqual(self.catalog.supporter_badges(1031000250, 3), '[空中用]/[水中用]')
        info = self.catalog.supporter_info(1370000750, 80, 3)
        self.assertEqual((info.hp, info.attack), (3360, 224))
        self.assertEqual([label for label, _ in info.leaders], ['[指挥官机]', '[劲敌]&[指挥官机]'])
        self.assertTrue(info.use_max)
        self.assertIn('28%', info.leaders[0][1])
        self.assertIn('44%', info.leaders[1][1])

    def test_leader_bonus_matches_units_for_each_supporter(self):
        # Spartan accepts either terrain tag. Kati also has a separate commander-only tier.
        self.assertTrue(self.catalog.receives_leader_bonus(1001000400, 1031000250, 3))
        self.assertTrue(self.catalog.receives_leader_bonus(1001001800, 1031000250, 3))
        self.assertFalse(self.catalog.receives_leader_bonus(1110000550, 1031000250, 3))
        self.assertFalse(self.catalog.receives_leader_bonus(1001001100, 1370000750, 3))
        self.assertTrue(self.catalog.receives_leader_bonus(1009000110, 1370000750, 3))
        self.assertTrue(self.catalog.receives_leader_bonus(1013000800, 1370000750, 3))
