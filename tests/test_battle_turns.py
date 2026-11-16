import unittest
from dataclasses import asdict
from battle_headless.turns import OriginalTurnClock,PhaseValue,ChanceStepState
from battle_headless.account import DEFAULT_SNAPSHOT,import_formation,build_account_env


class TurnTests(unittest.TestCase):
    def test_standard_phase_cycle_and_duration_identity(self):
        clock=OriginalTurnClock()
        self.assertEqual([(v.turn,v.id) for v in [clock.advance() for _ in range(6)]],
                         [(1,4),(1,2),(1,5),(1,3),(1,6),(2,0)])
        self.assertFalse(PhaseValue(1,0).is_turn_progressed(PhaseValue(1,2)))
        self.assertFalse(PhaseValue(1,0).is_turn_progressed(PhaseValue(2,2)))
        self.assertTrue(PhaseValue(1,0).is_turn_progressed(PhaseValue(2,0)))

    def test_chance_step_priority_and_one_time_carry(self):
        state=ChanceStepState(correction=1,once=1,guaranteed_once=1)
        self.assertEqual(state.maximum(),4)
        self.assertEqual([state.try_weapon_used(True) for _ in range(5)],
                         [(True,0),(True,0),(True,15),(True,23),(False,0)])
        state.init_phase()
        self.assertEqual((state.used,state.once,state.guaranteed_once),(0,0,0))
        self.assertEqual(state.maximum(),2)
        self.assertEqual(ChanceStepState(body_type=2).try_weapon_used(True),(False,0))
        self.assertEqual(ChanceStepState().try_weapon_used(False),(False,0))
        self.assertEqual(ChanceStepState(weapon_used_guaranteed=True).try_weapon_used(False),(True,0))

    def test_dead_phase_reset_preserves_existence_and_count(self):
        state=ChanceStepState(existence=2,used=3,once=2,has_moved=True,can_move_after_attack=True)
        state.init_phase()
        self.assertEqual((state.existence,state.used,state.once),(2,3,2))
        self.assertFalse(state.has_moved or state.can_move_after_attack)

    @unittest.skipUnless(DEFAULT_SNAPSHOT.exists(),'Requires the private local formation capture')
    def test_empty_phases_preserve_actual_turn_count(self):
        env,stage=build_account_env(import_formation())
        first=env.units['account_1_1'];first['acted']=first['moved']=True
        first['chance_step'].update(used=2,once=1)
        a=env.step(dict(type='end_turn'))['event']
        self.assertEqual([(p['turn'],p['side']) for p in a['phase_transitions']],[(1,4),(1,2)])
        self.assertEqual((env.turn,env.active_team),(1,'enemy'))
        b=env.step(dict(type='end_turn'))['event']
        self.assertEqual([(p['turn'],p['side']) for p in b['phase_transitions']],[(1,5),(1,3),(1,6),(2,0)])
        self.assertEqual((env.turn,env.active_team),(2,'player'))
        self.assertFalse(first['acted'] or first['moved'])
        self.assertEqual((first['chance_step']['used'],first['chance_step']['once']),(0,1))

    @unittest.skipUnless(DEFAULT_SNAPSHOT.exists(),'Requires the private local formation capture')
    def test_kill_creates_new_step_and_nonkill_finishes_it(self):
        env,stage=build_account_env(import_formation())
        unit=env.units['account_1_1'];target=next(u for u in env.units.values() if u['team']=='enemy')
        weapon=unit['weapons'][0];weapon.update(en=0,range_min=1,range_max=999)
        class Hit:
            last_roll=0
            def evaluate(self,rate):return True
        env.rng=Hit();target['hp']=1
        result=env.step(dict(type='attack',unit=unit['id'],target=target['id'],weapon=weapon['id']))
        self.assertTrue(result['event']['chance_step']['earned'])
        self.assertFalse(unit['acted'])
        self.assertEqual(unit['chance_step']['used'],1)
        self.assertEqual(unit['scalar_corrections']['ChanceStepMaxCount'],1)
        target=next(u for u in env.units.values() if u['team']=='enemy' and u['hp']>0)
        class Miss:
            last_roll=99
            def evaluate(self,rate):return False
        env.rng=Miss()
        result=env.step(dict(type='attack',unit=unit['id'],target=target['id'],weapon=weapon['id']))
        self.assertFalse(result['event']['chance_step']['earned'])
        self.assertTrue(unit['acted'])

    @unittest.skipUnless(DEFAULT_SNAPSHOT.exists(),'Requires the private local formation capture')
    def test_ordinary_own_phase_recovers_all_living_units_without_passives(self):
        env,stage=build_account_env(import_formation(),owner_passives=False)
        own=env.units['account_1_1'];enemy=next(u for u in env.units.values() if u['team']=='enemy')
        dead=next(u for u in env.units.values() if u['team']=='enemy' and u is not enemy)
        for unit in [own,enemy,dead]:
            unit['en']=0;unit['support_budget']=dict(attack_used=2,total_attack_used=9,attack_once=1)
        dead['hp']=0
        env._after_phase_start()
        self.assertEqual([u['en'] for u in [own,enemy,dead]],[0,0,0])
        env.step(dict(type='end_turn'))
        self.assertEqual([u['en'] for u in [own,enemy,dead]],[0,0,0])
        result=env.step(dict(type='end_turn'))
        for unit in [own,enemy]:
            self.assertEqual(unit['en'],(unit['max_en']+9)//10)
            self.assertEqual(unit['support_budget']['attack_used'],0)
            self.assertEqual(unit['support_budget']['total_attack_used'],9)
            self.assertEqual(unit['support_budget']['attack_once'],1)
        self.assertEqual(dead['en'],0)
        self.assertEqual(dead['support_budget']['attack_used'],2)
        self.assertTrue(any(e.get('source')=='ordinary_own_phase_start' for e in result['event']['phase_start_effects']))

    @unittest.skipUnless(DEFAULT_SNAPSHOT.exists(),'Requires the private local formation capture')
    def test_enemy_group_recovery_runs_at_ordinary_own_phase_start(self):
        env,stage=build_account_env(import_formation())
        source=next(u for u in env.units.values() if u.get('timed_passive_ids'))
        enemy=next(u for u in env.units.values() if u['team']=='enemy' and u is not source)
        own=env.units['account_1_1']
        for unit in [source,enemy,own]:unit['hp']=unit['max_hp']//2
        before={u['id']:u['hp'] for u in [source,enemy,own]}
        env.step(dict(type='end_turn'))
        self.assertEqual(before,{u['id']:u['hp'] for u in [source,enemy,own]})
        env.step(dict(type='end_turn'))
        for unit in [source,enemy]:
            self.assertGreater(unit['hp'],before[unit['id']])
        self.assertEqual(own['hp'],before[own['id']])


if __name__=='__main__':unittest.main()
