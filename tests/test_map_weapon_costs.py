from copy import deepcopy
import unittest
from battle_headless.map_weapon_costs import costs
from battle_headless.map_weapon_runtime import use_map_weapon


class MapWeaponCostTests(unittest.TestCase):
    def unit(self):return dict(hp=90,max_hp=101,en=80,max_en=100,mp=12)
    def weapon(self,mp=20,hp=10):return dict(main_weapon_id=17,map_level_stats=dict(en=30,mp_consumption=mp,hp_consumption_rate=hp))
    def test_map_mp_limit_is_9999_not_normal_weapon_twelve(self):
        self.assertEqual(costs(self.unit(),self.weapon(),[])['mp_cost'],20)
        self.assertEqual(costs(self.unit(),self.weapon(),[dict(MpValue=20000)])['mp_cost'],9999)
        self.assertEqual(costs(self.unit(),self.weapon(2147483647),[dict(MpValue=1)])['mp_cost'],0)
    def test_nullable_zero_and_cost_created_by_corrections(self):
        result=costs(self.unit(),self.weapon(0,0),[])
        self.assertIsNone(result['mp_cost']);self.assertIsNone(result['hp_cost'])
        result=costs(self.unit(),self.weapon(0,0),[dict(MpValue=1,HpRate=1)])
        self.assertEqual(result['mp_cost'],1);self.assertEqual(result['hp_cost'],2)
        result=costs(self.unit(),self.weapon(),[dict(MpValue=-20,HpRate=-10)])
        self.assertEqual(result['mp_cost'],0);self.assertEqual(result['hp_cost'],0)
    def test_filtering_en_free_hp_rounding_and_pure_repeat(self):
        unit=self.unit();weapon=self.weapon();before=deepcopy((unit,weapon))
        records=[dict(EnFreeCount=1),dict(condition=dict(WeaponAttributeTypes='2',AttackAttributeTypes=''),HpRate=50,MpValue=500)]
        result=costs(unit,weapon,records)
        self.assertEqual(result,dict(en_cost=0,mp_cost=20,hp_cost=11,hp_consumption_rate=10))
        self.assertEqual(costs(unit,weapon,records),result);self.assertEqual((unit,weapon),before)
    def test_costs_feed_existing_mutator_with_deferred_mp_and_shared_ammo(self):
        unit=self.unit();weapon=self.weapon(mp=2);resolved=costs(unit,weapon,[])
        arguments={k:resolved[k] for k in ('en_cost','mp_cost','hp_cost')}
        use_map_weapon(unit,weapon,[],**arguments)
        self.assertEqual((unit['hp'],unit['en'],unit['mp'],unit['pending_mp_change']),(79,50,12,-2))
        self.assertEqual(unit['map_weapon_used_counts'],{'17':1})
        before=deepcopy(unit);use_map_weapon(unit,weapon,[],predicting=True,**arguments)
        self.assertEqual(unit,before)


if __name__=='__main__':unittest.main()
