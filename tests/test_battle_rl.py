import random
import unittest
from copy import deepcopy
from battle_headless.engine import BattleEnv
from battle_headless.rl import BattleRL


class Units:
    sha256 = 'test'

    def unit(self, unit_id, instance, team, position):
        return dict(id=instance, unit_id=unit_id, team=team, position=list(position),
                    hp=100, max_hp=100, en=100, max_en=100, attack=100,
                    defense=100, mobility=100, movement=2, moved=False, acted=False,
                    weapons=[dict(id=1, power=200, en=1, hit=100, critical=0,
                                  range_min=1, range_max=2)])


def factory(seed):
    env = BattleEnv(Units())
    env.reset(dict(width=4, height=3, max_turns=5, units=[
        dict(id='a', unit_id=1, team='player', position=[0, 1]),
        dict(id='b', unit_id=1, team='enemy', position=[3, 1])]), seed)
    return env


class RLTests(unittest.TestCase):
    def test_mask_matches_engine_and_illegal_is_atomic(self):
        env = BattleRL(factory)
        obs, _ = env.reset(seed=9)
        valid = {env._key(env.actions[i]) for i, v in enumerate(obs['action_mask']) if v}
        self.assertEqual(valid, {env._key(a) for a in env.env.legal_actions()})
        before = deepcopy(env.env.replay())
        invalid = next(i for i, v in enumerate(obs['action_mask']) if not v)
        with self.assertRaises(ValueError):
            env.step(invalid)
        self.assertEqual(before, env.env.replay())

    def test_seed_repeatability_and_bounded_episodes(self):
        env = BattleRL(factory, max_steps=4)
        def rollout():
            obs, _ = env.reset(seed=7)
            rng = random.Random(12)
            trace = []
            while not env._done():
                index = rng.choice([i for i, v in enumerate(obs['action_mask']) if v])
                result = env.step(index)
                obs = result[0]
                trace.append(result)
            self.assertLessEqual(env.steps, 4)
            self.assertFalse(any(obs['action_mask']))
            return trace
        self.assertEqual(rollout(), rollout())

    def test_enemy_auto_and_terminal_reward(self):
        env = BattleRL(factory)
        env.env.units['b']['position'] = [1, 1]
        attack = next(i for i, a in enumerate(env.actions)
                      if a['type'] == 'attack' and env.action_masks()[i])
        _, reward, terminated, truncated, info = env.step(attack)
        self.assertTrue(terminated)
        self.assertFalse(truncated)
        self.assertEqual(info['winner'], 'player')
        self.assertAlmostEqual(reward, 1.999)
        with self.assertRaises(RuntimeError):
            env.step(0)
        env.reset(seed=1)
        env.step(0)
        self.assertTrue(env._done() or env.env.active_team == 'player')
        self.assertGreater(len(env.env.events), 1)


if __name__ == '__main__':
    unittest.main()
