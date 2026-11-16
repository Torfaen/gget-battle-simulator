import unittest
from copy import deepcopy
from battle_headless.engine import BattleEnv
from battle_headless.weapon_corrections import mp_consumption,hp_consumption_rate,hp_consumption
from battle_headless.weapon_use import use_weapon,reflect_mp_change
from battle_headless.stage_smoke import ControlledRules


class CostTests(unittest.TestCase):
    def unit(self):return dict(hp=900,max_hp=1000,en=70,max_en=100,mp=8)

    def test_nullable_zero_and_cost_caps(self):
        self.assertIsNone(mp_consumption(0,[],{}))
        self.assertEqual(mp_consumption(5,[dict(MpValue=-5)],{}),0)
        self.assertEqual(mp_consumption(5,[dict(MpValue=100)],{}),12)
        self.assertIsNone(hp_consumption_rate(0,[],{}))
        self.assertEqual(hp_consumption_rate(10,[dict(HpRate=-10)],{}),0)
        self.assertEqual(hp_consumption_rate(90,[dict(HpRate=100)],{}),100)

    def test_hp_cost_uses_maximum_and_ceils(self):
        self.assertIsNone(hp_consumption(1000,None))
        self.assertEqual(hp_consumption(1001,10),101)

    def test_use_order_and_deferred_mp_reflection(self):
        unit=self.unit();event=use_weapon(unit,20,3,100)
        self.assertEqual((unit['en'],unit['hp'],unit['mp'],unit['pending_mp_change']),(50,800,8,-3))
        self.assertEqual(event['resource_before']['en'],70)
        reflect_mp_change(unit)
        self.assertEqual((unit['mp'],unit['pending_mp_change'],unit['mp_change_since_battle_start']),(5,0,-3))
        reflect_mp_change(unit)
        self.assertEqual(unit['mp_change_since_battle_start'],-3)

    def test_prediction_skips_mutation_and_mp_locks_only_lock_mp(self):
        unit=self.unit();before=dict(unit)
        use_weapon(unit,20,3,100,predicting=True)
        self.assertEqual(unit,before)
        for field in ['AntiMpChangeCount','MpLimitBreakCount']:
            unit=self.unit();unit['scalar_corrections']={field:1}
            use_weapon(unit,20,3,100);reflect_mp_change(unit)
            self.assertEqual((unit['en'],unit['hp'],unit['mp']),(50,800,8))

    def test_raw_hp_mutator_keeps_original_lower_bound_behavior(self):
        unit=self.unit();use_weapon(unit,1000,None,1000)
        self.assertEqual((unit['en'],unit['hp']),(0,-100))

    def test_connected_resource_availability(self):
        rules=ControlledRules.__new__(ControlledRules)
        rules.kernel=type('Kernel',(),{'tension':staticmethod(lambda mp:2)})()
        unit=self.unit();weapon=dict(_combat_selection={},en=70,mp_cost=8,hp_cost=899)
        self.assertTrue(rules.weapon_available(unit,None,weapon))
        for field,value in [('en',71),('mp_cost',9),('hp_cost',900)]:
            self.assertFalse(rules.weapon_available(unit,None,dict(weapon,**{field:value})))
        unit['scalar_corrections']={'AttackInabilityCount':1}
        self.assertFalse(rules.weapon_available(unit,None,weapon))

    def test_attack_calculates_before_costs_and_reflects_mp_at_end(self):
        observed=[]
        class Rules(ControlledRules):
            def __init__(self):
                self.kernel=type('Kernel',(),{'tension':staticmethod(lambda mp:2)})()
            def effective_weapon(self,unit,target,weapon):return dict(weapon,_combat_selection={})
            def resolve_attack(self,unit,target,weapon,rng):
                observed.append((unit['en'],unit['hp'],unit['mp']))
                return dict(hit=True,critical=False,damage=5)
        class Units:
            sha256='controlled-fixture'
            def unit(self,uid,instance,team,position):
                return dict(id=instance,unit_id=uid,team=team,position=deepcopy(position),
                    hp=100,max_hp=100,en=30,max_en=30,mp=8,movement=0,moved=False,acted=False,
                    weapons=[dict(id=1,en=20,mp_cost=3,hp_cost=10,range_min=1,range_max=1)])
        env=BattleEnv(Units(),Rules())
        env.reset(dict(width=2,height=1,units=[dict(id='a',unit_id=1,team='player',position=[0,0]),
            dict(id='b',unit_id=2,team='enemy',position=[1,0])]))
        env.step(dict(type='attack',unit='a',target='b',weapon=1))
        self.assertEqual(observed,[(30,100,8)])
        self.assertEqual((env.units['a']['en'],env.units['a']['hp'],env.units['a']['mp']),(10,90,5))
        self.assertEqual(env.units['b']['hp'],95)


if __name__=='__main__':unittest.main()
