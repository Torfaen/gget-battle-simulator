import unittest
from types import SimpleNamespace
from battle_headless.trait_turn_progress import standard_satisfies,master_league_satisfies
from battle_headless.trait_consumption import attach_runtime_trait_consumption
from battle_headless.turns import OriginalTurnClock,PhaseValue


class TraitTurnProgressTests(unittest.TestCase):
    def test_standard_uses_last_consumption_phase_and_requires_same_phase_id(self):
        group=dict(turn_progress_consumed_phase_value=dict(turn=1,id=0),created_phase_value=dict(turn=0,id=4))
        self.assertFalse(standard_satisfies(group,PhaseValue(1,0)))
        self.assertFalse(standard_satisfies(group,PhaseValue(2,4)))
        self.assertTrue(standard_satisfies(group,PhaseValue(2,0)))
    def test_league_uses_created_phase_and_dead_unit_ids_lazily(self):
        group=dict(created_phase_value=PhaseValue(1,9),turn_progress_consumed_phase_value=PhaseValue(2,9));calls=[]
        def provider(p):calls.append(p);return [7,9]
        self.assertTrue(master_league_satisfies(group,PhaseValue(2,9),provider));self.assertEqual(calls,[])
        self.assertTrue(master_league_satisfies(group,PhaseValue(2,7),provider));self.assertEqual(len(calls),1)
        self.assertFalse(master_league_satisfies(group,PhaseValue(1,7),provider))
        with self.assertRaises(NotImplementedError):master_league_satisfies(group,PhaseValue(2,7),None)
    def test_standard_clock_consumes_once_per_matching_turn_and_removes_expired_effect(self):
        unit=dict(id='a',existence=0,runtime_trait_effects={},buff=100)
        trait=dict(runtime_trait_id=2**40,base_limit=2,additional_limit=0,used_count=0,trait_type=35,action_timing=3)
        group=dict(owner_unit_id='a',values=[trait],enabled_internal=True,enabled_while_all_traits_usable=False,
            created_phase_value=PhaseValue(1,0),turn_progress_consumed_phase_value=PhaseValue(1,0))
        unit['runtime_trait_effects'][trait['runtime_trait_id']]=dict(can_remove=lambda t,u:True,
            remove=lambda t,u:u.update(buff=0))
        clock=OriginalTurnClock();env=SimpleNamespace(units={'a':unit});groups={2**41:group}
        attach_runtime_trait_consumption(env,groups,lambda:PhaseValue(clock.turn,clock.side),turn_progress_policy='standard')
        for _ in range(6):
            env.consume_runtime_trait_count(3);clock.advance()
        self.assertEqual(trait['used_count'],0)
        env.consume_runtime_trait_count(3);env.consume_runtime_trait_count(3)
        self.assertEqual(trait['used_count'],1);self.assertEqual(unit['buff'],100)
        for _ in range(6):clock.advance()
        result=env.consume_runtime_trait_count(3)
        self.assertEqual(trait['used_count'],2);self.assertEqual(unit['buff'],0)
        self.assertEqual(result['removed_set_ids'],[2**41]);self.assertEqual(groups,{})


if __name__=='__main__':unittest.main()
