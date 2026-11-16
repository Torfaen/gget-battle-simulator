import unittest
from dataclasses import asdict
from battle_headless.turns import ChanceStepState
from battle_headless.map_step import begin_step_resolution,finish_step_resolution


class MapStepTests(unittest.TestCase):
    def unit(self,**options):
        return dict(chance_step=asdict(ChanceStepState(**options)),scalar_corrections={})
    def test_kill_does_not_grant_ordinary_chance_step(self):
        unit=self.unit();step={}
        self.assertFalse(begin_step_resolution(unit,step,can_map_weapon_move_after_attack=False)['earned'])
        self.assertFalse(finish_step_resolution(unit,step)['earned'])
        self.assertEqual(unit['existence'],1);self.assertTrue(unit['acted'])
        self.assertEqual(unit['chance_step']['used'],0)
    def test_guaranteed_step_consumes_only_once_across_two_attempts(self):
        unit=self.unit();unit['scalar_corrections']['GuaranteedChanceStepCount']=1;step={};calls=[]
        hooks={name:lambda u,t:calls.append(t) for name in
            ('find_display_trait_on_chance_step_earned','consume_on_guaranteed_chance_step_used')}
        first=begin_step_resolution(unit,step,can_map_weapon_move_after_attack=True,lifecycle=hooks)
        self.assertTrue(first['earned']);self.assertEqual(first['pending_lifecycle'],[])
        self.assertTrue(finish_step_resolution(unit,step,lifecycle=hooks)['earned'])
        self.assertEqual(unit['chance_step']['used'],1);self.assertEqual(calls,[0,0])
        self.assertFalse(unit['chance_step']['can_move_after_map_attack'])
    def test_late_trait_grant_is_seen_on_retry(self):
        unit=self.unit();step={}
        begin_step_resolution(unit,step,can_map_weapon_move_after_attack=False)
        unit['chance_step']['guaranteed_once']=1
        result=finish_step_resolution(unit,step)
        self.assertTrue(result['earned']);self.assertEqual(result['consumed_timing'],23)
        self.assertEqual(unit['chance_step']['guaranteed_once'],0)
    def test_map_move_permission_reopens_without_erasing_other_flags(self):
        unit=self.unit(can_move_after_attack=True);step={}
        begin_step_resolution(unit,step,can_map_weapon_move_after_attack=True)
        finish_step_resolution(unit,step)
        self.assertFalse(unit['acted']);self.assertEqual(unit['existence'],0)
        self.assertTrue(unit['chance_step']['can_move_after_attack'])
        self.assertTrue(unit['chance_step']['can_move_after_map_attack'])
    def test_dead_special_state_is_preserved_and_move_flags_clear(self):
        unit=self.unit(existence=2,can_move_after_attack=True);step={}
        begin_step_resolution(unit,step,can_map_weapon_move_after_attack=True)
        finish_step_resolution(unit,step)
        self.assertEqual(unit['existence'],2)
        self.assertFalse(unit['chance_step']['can_move_after_map_attack'])
        self.assertFalse(unit['chance_step']['can_move_after_attack'])


if __name__=='__main__':unittest.main()
