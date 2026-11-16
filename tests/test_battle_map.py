from types import SimpleNamespace
import unittest
from battle_headless.battle_map import BattleMap
from battle_headless.geometry import Area, MapGeometry, ExcludeArea
from battle_headless.occupancy import UnitFootprint
from test_battle_rl import factory


def unit(uid, position):
    return dict(id=uid, hp=100, position=position, direction=2, movement=4)


class BattleMapTests(unittest.TestCase):
    def setup_map(self, footprints=None, geometry=None):
        a, b = unit('a', [0, 0]), unit('b', [3, 0])
        env = SimpleNamespace(width=8, height=6, blocked=set(), units={'a': a, 'b': b})
        return BattleMap(env, footprints or {'a': UnitFootprint(1), 'b': UnitFootprint(2)}, geometry)

    def test_range_hits_far_edge_of_large_target(self):
        board = self.setup_map()
        weapon = dict(range_min=4, range_max=4)
        self.assertTrue(board.within_range(board.env.units['a'], board.env.units['b'], weapon))
        weapon.update(range_min=6, range_max=6)
        self.assertFalse(board.within_range(board.env.units['a'], board.env.units['b'], weapon))

    def test_large_attacker_range_uses_bounding_area(self):
        board = self.setup_map({'a': UnitFootprint(2), 'b': UnitFootprint(1)})
        self.assertTrue(board.within_range(board.env.units['a'], board.env.units['b'],
                                         dict(range_min=2, range_max=2)))

    def test_movement_cannot_enter_any_live_occupied_cell(self):
        board = self.setup_map()
        reachable = board.reachable(board.env.units['a'])
        self.assertNotIn((3, 1), reachable)  # Boss pivot is (3,0), occupies (3,1).
        board.env.units['b']['hp'] = 0
        self.assertIn((3, 1), board.reachable(board.env.units['a']))

    def test_large_mover_cannot_clip_border_or_obstacles(self):
        board = self.setup_map({'a': UnitFootprint(2), 'b': UnitFootprint(1)})
        board.env.units['b']['hp'] = 0
        board.env.blocked = {(2, 1)}
        self.assertFalse(board.can_place(board.env.units['a'], (1, 0)))
        self.assertFalse(board.can_place(board.env.units['a'], (7, 0)))
        self.assertTrue(board.can_place(board.env.units['a'], (4, 2)))

    def test_closed_region_clips_target_and_movement(self):
        geometry = MapGeometry(Area(0, 0, 8, 6),
            (ExcludeArea(1, Area(3, 0, 2, 2)),))
        board = self.setup_map(geometry=geometry)
        weapon = dict(range_min=1, range_max=8)
        self.assertFalse(board.within_range(board.env.units['a'], board.env.units['b'], weapon))
        board.geometry = MapGeometry(geometry.bounds, (ExcludeArea(1, Area(3, 0, 2, 2), True),))
        self.assertTrue(board.within_range(board.env.units['a'], board.env.units['b'], weapon))

    def test_single_cell_movement_and_legal_actions_match_legacy(self):
        env = factory(1)
        before = env.legal_actions()
        for u in env.units.values(): u['direction'] = 2
        board = BattleMap(env, {uid: UnitFootprint(1) for uid in env.units})
        env._reachable, env._within_range = board.reachable, board.within_range
        self.assertEqual(before, env.legal_actions())
        # Both attack selection paths now call the same map range service.
        env.units['b']['position'] = [2, 1]
        self.assertTrue(any(a['type'] == 'attack' for a in env.legal_actions()))


if __name__ == '__main__':
    unittest.main()
