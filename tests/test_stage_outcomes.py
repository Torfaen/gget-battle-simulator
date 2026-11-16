from copy import deepcopy
from types import SimpleNamespace
import unittest
from battle_headless.stage_outcomes import attach_stage_outcomes
from battle_headless.rl import BattleRL
from test_battle_rl import factory


def condition(identifier, category, group, kind, *, side=None, target=0):
    return dict(Id=identifier, MapStageConditionTypeIndex=category,
                MapStageConditionGroupId=group, IngameEventConditionTypeIndex=kind,
                IngameEventConditionParameterTypeAIndex=5 if side is not None else 0,
                ParameterValueA='' if side is None else str(side), TargetValue=target)


ROWS = [condition(1, 1, 0, 1, side=2), condition(2, 2, 0, 1, side=0),
        condition(3, 2, 1, 7, target=3)]


def stage_env(seed=0, rows=ROWS):
    env = factory(seed)
    scenario = deepcopy(env.scenario)
    scenario.update(phase_clock='original-standard', phase_teams={'0': 'player', '2': 'enemy'}, max_turns=3)
    env.reset(scenario, seed)
    attach_stage_outcomes(env, SimpleNamespace(conditions=rows))
    return env


class StageOutcomeTests(unittest.TestCase):
    def test_turn_count_means_completed_turns_and_is_terminal(self):
        env = stage_env()
        env.turn = env.phase_clock.turn = 3
        self.assertIsNone(env.stage_outcome_runtime.check())
        env.step(dict(type='end_turn'))  # Enemy phase of turn 3 is still playable.
        self.assertFalse(env.terminated or env.truncated)
        event = env.step(dict(type='end_turn'))['event']
        self.assertTrue(env.terminated)
        self.assertFalse(env.truncated)
        self.assertEqual(env.winner, 'enemy')
        self.assertEqual(event['stage_outcome']['elapsed_turns'], 3)
        self.assertEqual(event['stage_outcome']['condition_ids'], [3])
        self.assertEqual(env.legal_actions(), [])

    def test_kill_all_and_failure_precedence(self):
        for own_alive, enemy_alive, expected in [(True, False, 'player'), (False, True, 'enemy'),
                                                 (False, False, 'enemy')]:
            env = stage_env()
            env.units['a']['hp'] = int(own_alive)
            env.units['b']['hp'] = int(enemy_alive)
            env.stage_outcome_runtime.check()
            self.assertEqual(env.winner, expected)
            self.assertTrue(env.terminated)

    def test_attack_outcome_is_recorded_and_latched(self):
        env = stage_env()
        env.units['b']['position'] = [1, 1]
        env.units['b']['hp'] = 1
        action = next(a for a in env.legal_actions() if a['type'] == 'attack')
        event = env.step(action)['event']
        self.assertEqual(event['stage_outcome']['result'], 'victory')
        env.units['a']['hp'] = 0
        self.assertEqual(env.stage_outcome_runtime.check()['result'], 'victory')

    def test_and_within_groups_or_between_groups(self):
        rows = [condition(1, 1, 0, 1, side=2), condition(2, 1, 0, 7, target=2),
                condition(3, 1, 1, 7, target=5)]
        env = stage_env(rows=rows)
        env.units['b']['hp'] = 0
        self.assertIsNone(env.stage_outcome_runtime.check())
        env.turn = 3
        self.assertEqual(env.stage_outcome_runtime.check()['condition_ids'], [1, 2])
        env = stage_env(rows=rows);env.turn = 6
        self.assertEqual(env.stage_outcome_runtime.check()['condition_ids'], [3])

    def test_unsupported_conditions_reject_at_attach(self):
        with self.assertRaises(NotImplementedError):
            stage_env(rows=[condition(1, 1, 0, 99)])

    def test_rl_timeout_gets_defeat_reward_instead_of_truncation(self):
        def no_enemy_attack(seed):
            env = stage_env(seed)
            env.units['b']['weapons'] = []
            return env
        env = BattleRL(no_enemy_attack)
        _, reward, terminated, truncated, info = env.step(0)
        while not terminated and not truncated:
            _, reward, terminated, truncated, info = env.step(0)
        self.assertTrue(terminated)
        self.assertFalse(truncated)
        self.assertLessEqual(reward, -1)
        self.assertEqual(info['stage_outcome']['result'], 'defeat')
        self.assertEqual(info['stage_outcome']['predicate_types'], [7])
        limited = BattleRL(no_enemy_attack, max_steps=1)
        _, reward, terminated, truncated, info = limited.step(0)
        self.assertFalse(terminated)
        self.assertTrue(truncated)
        self.assertIsNone(info['stage_outcome'])
        self.assertAlmostEqual(reward, -0.001)



    def test_moment_success_and_failure_priority_without_ending_battle(self):
        from battle_headless.ai_configuration import determine_ai_type
        for failed in (False, True):
            rows = ROWS + [condition(4, 3, 0, 7, target=1)]
            if failed:
                rows += [condition(5, 4, 0, 7, target=1)]
            env = stage_env(rows=rows)
            self.assertEqual(determine_ai_type(env.moment_trigger), 1)
            env.turn = 2
            self.assertIsNone(env.stage_outcome_runtime.check())
            self.assertFalse(env.terminated)
            self.assertEqual(env.moment_trigger['is_failure'], failed)
            self.assertEqual(env.moment_trigger['is_success'], not failed)
            self.assertEqual(determine_ai_type(env.moment_trigger), 2)
            self.assertEqual(env.stage_outcome_runtime.check_moment()['condition_ids'],
                             [5] if failed else [4])
            env.turn = 1
            self.assertEqual(determine_ai_type(env.moment_trigger), 2)

    def test_moment_group_and_or_and_failure_only_has_no_trigger(self):
        rows = ROWS + [condition(4, 3, 0, 7, target=1),
                       condition(5, 3, 0, 1, side=2),
                       condition(6, 3, 1, 7, target=2)]
        env = stage_env(rows=rows)
        env.turn = 2
        self.assertIsNone(env.stage_outcome_runtime.check_moment())
        env.turn = 3
        self.assertEqual(env.stage_outcome_runtime.check_moment()['condition_ids'], [6])
        env = stage_env(rows=ROWS + [condition(5, 4, 0, 7, target=0)])
        self.assertIsNone(env.stage_outcome_runtime.check_moment())
        self.assertFalse(env.moment_trigger['exists'])

    def test_phase_progress_updates_moment_observation(self):
        env = stage_env(rows=ROWS + [condition(4, 3, 0, 7, target=1)])
        env.step(dict(type='end_turn'))
        self.assertFalse(env.moment_trigger['is_success'])
        env.step(dict(type='end_turn'))
        self.assertTrue(env.observe()['moment_trigger']['is_success'])
        self.assertFalse(env.terminated or env.truncated)

    def test_imported_completed_moment_is_preserved_and_new_env_resets(self):
        rows = ROWS + [condition(4, 3, 0, 7, target=0)]
        env = stage_env(rows=rows)
        env.moment_trigger.update(is_failure=True, is_success=False)
        env.stage_outcome_runtime.check()
        self.assertTrue(env.moment_trigger['is_failure'])
        self.assertFalse(env.moment_trigger['is_success'])
        fresh = stage_env(rows=rows)
        self.assertFalse(fresh.moment_trigger['is_failure'])
        fresh.stage_outcome_runtime.check()
        self.assertTrue(fresh.moment_trigger['is_success'])

    def test_unsupported_moment_predicate_rejects_at_attach(self):
        with self.assertRaises(NotImplementedError):
            stage_env(rows=ROWS + [condition(4, 3, 0, 99)])


if __name__ == '__main__': unittest.main()
