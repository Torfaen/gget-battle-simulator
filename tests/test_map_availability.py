from dataclasses import replace
from types import SimpleNamespace
import unittest
from battle_headless.map_availability import MapAvailabilityInputs,before_check,after_check,availability


class MapAvailabilityTests(unittest.TestCase):
    def state(self,**changes):
        base=MapAvailabilityInputs(True,True,False,False,50,20,12,None,2,0,100,None,False,False,1)
        return replace(base,**changes)
    def test_all_failures_accumulate_in_native_order(self):
        state=self.state(has_remaining_ammo=False,has_capability=False,attack_inability=True,en=0,
            mp_cost=20,tension=2,required_tension=4,hp_cost=100,has_moved=True,range_type=4,movement_inability=True)
        self.assertEqual(tuple(map(int,before_check(state))),(3,8,6,1,2,4,11,7,5))
    def test_post_attack_move_is_an_attack_block_and_no_early_return(self):
        self.assertEqual(tuple(map(int,before_check(self.state(can_move_after_any_attack=True,en=0)))),(6,1))
    def test_present_zero_hp_cost_compares_even_at_zero_hp(self):
        self.assertEqual(tuple(map(int,before_check(self.state(hp=0,hp_cost=0)))),(11,))
        self.assertEqual(before_check(self.state(hp=0,hp_cost=None)),())
    def test_movement_inability_only_moving_map_and_move_permission(self):
        self.assertEqual(before_check(self.state(movement_inability=True)),())
        self.assertEqual(before_check(self.state(has_moved=True,can_use_after_move=True)),())
    def test_after_check_base_empty_and_moving_requires_resolved_geometry(self):
        for kind in (1,2,3,5,6):self.assertEqual(after_check(kind),())
        with self.assertRaises(NotImplementedError):after_check(4)
        self.assertEqual(after_check(4,moving_immovable=False),())
        self.assertEqual(tuple(map(int,after_check(4,moving_immovable=True))),(10,))
    def test_normalized_inputs_connect_costs_ammo_and_distinct_capability(self):
        env=SimpleNamespace(rules=SimpleNamespace(kernel=SimpleNamespace(tension=lambda mp:2)))
        unit=dict(hp=100,max_hp=100,en=50,max_en=100,mp=12)
        weapon=dict(main_weapon_id=1,map_range_type=1,required_tension=0,can_use_after_move=True,
            capability=dict(HasCapabilityInSpace=True,DamageEfficiencyInSpace=0),
            map_level_stats=dict(en=10,mp_consumption=20,hp_consumption_rate=0,ammo_capacity=1))
        result=availability(env,unit,weapon,[],has_moved=False,can_move_after_any_attack=False,standby_terrain_type=2)
        self.assertEqual(result['reasons'],[2]);self.assertEqual(result['costs']['mp_cost'],20)
        self.assertFalse(result['after_check_required']);self.assertFalse(result['complete_action_eligibility'])
        unit['map_weapon_used_counts']={'1':1}
        self.assertEqual(availability(env,unit,weapon,[],has_moved=False,can_move_after_any_attack=False,standby_terrain_type=2)['reasons'],[3,2])


if __name__=='__main__':unittest.main()
