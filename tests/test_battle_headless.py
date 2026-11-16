from copy import deepcopy
import json
from pathlib import Path
import unittest

from battle_headless import BattleEnv, MasterData
from battle_headless.__main__ import choose


class BattleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        examples = Path(__file__).resolve().parents[1] / 'examples'
        cls.master = MasterData(examples / 'demo_master.json')
        cls.scenario = json.loads((examples / 'demo_scenario.json').read_text())

    def env(self, scenario=None):
        env = BattleEnv(self.master)
        env.reset(scenario or self.scenario, seed=37)
        return env

    def test_master_links(self):
        u = self.env().units['ally']
        self.assertEqual(u['hp'], 100)
        self.assertTrue(any(w['id'] == 1 and w['power'] == 27 for w in u['weapons']))

    def test_illegal_atomic_and_observation_isolated(self):
        env = self.env()
        before, rng = env.observe(), env.rng.getstate()
        with self.assertRaises(ValueError):
            env.step({'type': 'attack', 'unit': 'enemy', 'target': 'ally', 'weapon': 0})
        self.assertEqual(before, env.observe())
        self.assertEqual(rng, env.rng.getstate())
        self.assertEqual(env.events, [])
        before['units'][0]['hp'] = 0
        self.assertGreater(env.units['ally']['hp'], 0)

    def test_movement_obstacles_and_phase(self):
        env = self.env()
        moves = [a for a in env.legal_actions() if a['type'] == 'move']
        self.assertNotIn([2, 2], [a['to'] for a in moves])
        env.step(moves[0])
        self.assertFalse(any(a['type'] == 'move' for a in env.legal_actions()))
        env.step({'type': 'end_turn'})
        self.assertEqual(env.active_team, 'enemy')
        env.step({'type': 'end_turn'})
        self.assertFalse(env.units['ally']['moved'])

    def test_attack_cost_and_terminal(self):
        s = deepcopy(self.scenario)
        s['units'][1]['position'] = [1, 1]
        env = self.env(s)
        a = next(a for a in env.legal_actions() if a['type'] == 'attack')
        weapon = next(w for w in env.units['ally']['weapons'] if w['id'] == a['weapon'])
        env.units['enemy']['hp'] = 1
        weapon['hit'] = 100
        en = env.units['ally']['en']
        result = env.step(a)
        self.assertEqual(env.units['ally']['en'], en-weapon['en'])
        self.assertEqual(result['event']['applied_damage'], 1)
        self.assertTrue(env.terminated)
        self.assertEqual(env.winner, 'player')
        self.assertEqual(env.legal_actions(), [])

    def test_seed_replay(self):
        env = self.env()
        while not (env.terminated or env.truncated):
            env.step(choose(env))
        replay = env.replay()
        other = self.env()
        for event in replay['events']:
            self.assertEqual(other.step(event['action'])['event'], event)
        self.assertEqual(other.observe(), replay['final'])

    def test_exhaustion_timeout_and_bad_setup(self):
        env = self.env()
        env.units['ally']['en'] = -1
        self.assertFalse(any(a['type'] == 'attack' for a in env.legal_actions()))
        s = deepcopy(self.scenario)
        s['max_turns'] = 1
        env = self.env(s)
        env.step({'type': 'end_turn'})
        self.assertTrue(env.truncated)
        self.assertFalse(env.terminated)
        s['units'][1]['position'] = s['units'][0]['position']
        with self.assertRaises(ValueError):
            self.env(s)


if __name__ == '__main__':
    unittest.main()
