import unittest
from copy import deepcopy
from battle_headless.map_drain import calculate_drained_value,apply_drained_value


class MapDrainTests(unittest.TestCase):
    def unit(self):return dict(hp=9,max_hp=100,en=3,max_en=20)
    def weapon(self):return dict(weapon_traits=[dict(WeaponTraitTypeIndex=k,TargetValue=50,TargetLimit=100) for k in (10,11)])
    def row(self,hp=None,barrier=None,en=None,hit=True,attack=True):
        return dict(calculation=dict(hit=hit,is_attack=attack,hp_damage=hp,hp_barrier_damage=barrier,en_damage=en))

    def test_multiple_targets_barriers_and_misses(self):
        rows=[self.row(15,5,3),self.row(10,0,4),self.row(999,999,999,False)]
        self.assertEqual(calculate_drained_value(self.unit(),self.weapon(),rows),
            dict(total_hp_damage=30,total_en_damage=7,drained_hp=15,drained_en=4))

    def test_empty_results_return_none_without_querying_actor_or_weapon(self):
        self.assertEqual(calculate_drained_value({}, {}, []),
            dict(total_hp_damage=0,total_en_damage=0,drained_hp=None,drained_en=None))

    def test_recovery_is_not_damage_and_present_zero_is_retained(self):
        result=calculate_drained_value(self.unit(),self.weapon(),[self.row(-50,None,-10,attack=False)])
        self.assertEqual((result['drained_hp'],result['drained_en']),(0,0))
        result=calculate_drained_value(self.unit(),dict(weapon_traits=[]),[self.row(10,5,2)])
        self.assertIsNone(result['drained_hp']);self.assertIsNone(result['drained_en'])

    def test_fold_int32_overflow_and_first_trait_only(self):
        weapon=self.weapon();weapon['weapon_traits'].insert(0,dict(WeaponTraitTypeIndex=10,TargetValue=100,TargetLimit=100))
        result=calculate_drained_value(self.unit(),weapon,[self.row(2147483647,1,0)])
        self.assertEqual(result['total_hp_damage'],-2147483648)
        # Native multiplication overflows before the cap/min and ceiling.
        self.assertEqual(result['drained_hp'],0)

    def test_overkill_uses_calculated_damage_then_recovery_clamps_actor(self):
        actor=self.unit();drained=calculate_drained_value(actor,self.weapon(),[self.row(1000,1000,100)])
        self.assertEqual((drained['drained_hp'],drained['drained_en']),(100,20))
        result=apply_drained_value(actor,drained)
        self.assertEqual((actor['hp'],actor['en']),(100,20))
        self.assertEqual((result['applied_hp_drain'],result['applied_en_drain']),(91,17))

    def test_prediction_keeps_actor_and_input_unchanged(self):
        actor=self.unit();drained=dict(drained_hp=20,drained_en=10)
        before=deepcopy((actor,drained));result=apply_drained_value(actor,drained,prediction=True)
        self.assertEqual((actor,drained),before)
        self.assertEqual((result['applied_hp_drain'],result['applied_en_drain']),(20,10))


if __name__=='__main__':unittest.main()
