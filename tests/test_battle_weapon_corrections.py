import unittest
from copy import deepcopy
from battle_headless.weapon_corrections import (percentage_delta,en_consumption,matches_weapon,
    select_combat_corrections,sum_shields)
from battle_headless.account import DEFAULT_SNAPSHOT,import_formation,build_account_env
from battle_headless.owner_passives import attach_owner_passives


class WeaponCorrectionTests(unittest.TestCase):
    def test_integer_percentage_and_free_en(self):
        records=[dict(EnRate=-20)]
        self.assertEqual(percentage_delta(records,'EnRate','EnValue',27,{}),-5)
        self.assertEqual(en_consumption(27,records,{}),22)
        self.assertEqual(en_consumption(27,records+[dict(EnFreeCount=1)],{}),0)
        self.assertEqual(en_consumption(27,[dict(EnValue=-100)],{}),0)
        self.assertEqual(en_consumption(9000,[dict(EnValue=9000)],{}),9999)

    def test_weapon_filter_wildcard_and_intersection(self):
        condition=dict(WeaponAttributeTypes='2',AttackAttributeTypes='1,3')
        self.assertTrue(matches_weapon(condition,dict(weapon_attributes=[1,2],attack_attributes=[3])))
        self.assertFalse(matches_weapon(condition,dict(weapon_attributes=[2],attack_attributes=[2])))
        self.assertTrue(matches_weapon(dict(WeaponAttributeTypes='0',AttackAttributeTypes=''),{}))

    @unittest.skipUnless(DEFAULT_SNAPSHOT.exists(),'Requires the private local formation capture')
    def test_en_discount_changes_legality_and_charges_effective_cost_once(self):
        env,stage=build_account_env(import_formation())
        unit=env.units['account_1_1'];target=next(u for u in env.units.values() if u['team']=='enemy')
        trait=deepcopy(next(t for t in unit['_owner_traits'] if t['TraitTypeIndex']==1))
        trait.update(Id=-100,ActiveConditionSetId=0,TraitTypeIndex=47,TraitValue=-20)
        unit['_owner_traits']=[trait]
        weapon=deepcopy(unit['weapons'][0]);weapon.update(en=27,hit=80,power=1000,range_min=1,range_max=999)
        unit['weapons']=[weapon]
        attach_owner_passives(env,stage);unit['en']=22
        action=dict(type='attack',unit=unit['id'],target=target['id'],weapon=weapon['id'])
        self.assertIn(action,env.legal_actions())
        result=env.step(action)
        self.assertEqual(result['event']['en_cost'],22)
        self.assertEqual(unit['en'],0)
        self.assertEqual(unit['weapons'][0]['en'],27)

    @unittest.skipUnless(DEFAULT_SNAPSHOT.exists(),'Requires the private local formation capture')
    def test_enemy_weapon_shield_and_support_action_conditions(self):
        env,stage=build_account_env(import_formation())
        defender=env.units['account_1_2'];attacker=next(u for u in env.units.values() if u['team']=='enemy')
        weapon=deepcopy(attacker['weapons'][0]);weapon.update(weapon_attributes=[2],attack_attributes=[1])
        selection=select_combat_corrections(stage.master,defender,attacker,weapon,
            action_type='NoAction',opponent_action_type='Attack',weapon_owner_id=attacker['id'])
        self.assertIn(100180301,selection['applied'])
        self.assertEqual(sum_shields(selection['shields'],'ReductionRate'),50)
        self.assertEqual(sum_shields(selection['shields'],'ReductionRate',True),0)
        weapon['weapon_attributes']=[1]
        physical=select_combat_corrections(stage.master,defender,attacker,weapon,
            action_type='NoAction',opponent_action_type='Attack',weapon_owner_id=attacker['id'])
        self.assertNotIn(100180301,physical['applied'])
        own_weapon=defender['weapons'][0]
        normal=select_combat_corrections(stage.master,defender,attacker,own_weapon)
        support=select_combat_corrections(stage.master,defender,attacker,own_weapon,action_type='SupportAttack')
        self.assertNotIn(200500402,normal['applied'])
        self.assertIn(200500402,support['applied'])


if __name__=='__main__':unittest.main()
