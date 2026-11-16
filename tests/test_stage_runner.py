import unittest
import json
from types import SimpleNamespace
from unittest.mock import patch

from battle_headless.random_original import OriginalRandom
from battle_headless.run_stage import RecordedRandom, run_battle, state_snapshot


class StageRunnerTests(unittest.TestCase):
    def env(self, mode='stage'):
        env = SimpleNamespace(units={}, turn=1, active_team='player', phase_clock=None,
            rng=OriginalRandom(37), terminated=False, truncated=False, winner=None,
            stage_outcome=None)
        def step(action):
            env.rng.evaluate(50)
            if mode == 'error':
                raise NotImplementedError('unsupported rule')
            env.terminated = mode == 'stage'
            env.truncated = mode == 'truncated'
            if env.terminated:
                env.stage_outcome = dict(result='defeat', predicate_types=[7])
                env.winner = 'enemy'
            return dict(event=dict(action=action))
        env.step = step
        return env

    def test_rng_trace_keeps_original_stream_and_short_circuits(self):
        original = OriginalRandom(37)
        recorded = RecordedRandom(original.getstate())
        for rate in (0, 100, 50, 99, -1, 101):
            self.assertEqual(original.evaluate(rate), recorded.evaluate(rate))
        self.assertEqual(recorded.getstate(), original.getstate())
        self.assertEqual(len(recorded.draws), 2)

    @patch('battle_headless.run_stage.initialization_blockers', return_value=[])
    def test_stage_outcome_replay_is_reproducible(self, _):
        first = run_battle(self.env(), [dict(type='end_turn')])
        second = run_battle(self.env(), [dict(type='end_turn')])
        self.assertEqual(first, second)
        self.assertTrue(first['completed'])
        self.assertFalse(first['exact_gameplay'])

    def test_default_refuses_incomplete_initialization(self):
        result = run_battle(self.env(), [dict(type='end_turn')])
        self.assertEqual(result['stop_reason'], 'initialization_blocked')
        self.assertEqual(result['steps'], [])

    def test_snapshot_keeps_deployment_and_existence_state(self):
        env=self.env()
        env.units['a']=dict(id='a',unit_id=100,team='player',position=[7,15],direction=3,
            standby_terrain_type=2,unit_standby_type=2,existence=0)
        snapshot=state_snapshot(env)
        self.assertEqual(snapshot['units'],[env.units['a']])
        env.units['a']['position'][0]=8
        self.assertEqual(snapshot['units'][0]['position'],[7,15])

    def test_provisional_stage_outcome_is_not_acceptance(self):
        result = run_battle(self.env(), [dict(type='end_turn')], allow_incomplete=True)
        self.assertTrue(result['reached_stage_outcome'])
        self.assertFalse(result['completed'])

    def test_snapshot_keeps_actual_trait_repository_and_serializable_callbacks(self):
        from battle_headless.trait_resource_effects import cannot_remove_resource
        from battle_headless.turns import PhaseValue
        env=self.env()
        env.runtime_trait_sets={7:dict(runtime_set_id=7,created_phase_value=PhaseValue(0,0),
            values=[dict(runtime_trait_id=11,used_count=2)])}
        env.units['a']=dict(id='a',runtime_trait_effects={11:dict(can_remove=cannot_remove_resource,
            stat_correction=dict(MaxHpValue=100))})
        result=state_snapshot(env)
        self.assertEqual(result['runtime_trait_groups'][7]['created_phase_value'],dict(turn=0,id=0))
        self.assertEqual(result['runtime_trait_groups'][7]['values'][0]['used_count'],2)
        effect=result['units'][0]['runtime_trait_effects'][11]
        self.assertEqual(effect['can_remove'],dict(callback='battle_headless.trait_resource_effects.cannot_remove_resource'))
        json.dumps(result)
        env.runtime_trait_sets[7]['values'][0]['used_count']=3
        self.assertEqual(result['runtime_trait_groups'][7]['values'][0]['used_count'],2)

    @patch('battle_headless.run_stage.initialization_blockers', return_value=[])
    def test_limits_exhaustion_errors_and_truncation_never_complete(self, _):
        for mode, actions, limit, reason in (
            ('idle', [], 10, 'script_exhausted'),
            ('idle', [dict(type='wait')], 1, 'action_limit'),
            ('truncated', [dict(type='end_turn')], 10, 'truncated'),
            ('error', [dict(type='attack')], 10, 'execution_error')):
            with self.subTest(mode=mode, reason=reason):
                result = run_battle(self.env(mode), actions, max_actions=limit)
                self.assertEqual(result['stop_reason'], reason)
                self.assertFalse(result['completed'])


if __name__ == '__main__':
    unittest.main()
