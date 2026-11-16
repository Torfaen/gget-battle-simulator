import unittest
from battle_headless.trait_consumption import consume_resolved_set,remove_from_unit,is_all_removable
from battle_headless.map_weapon_data import properties


class TraitCleanupTests(unittest.TestCase):
    def trait(self,identity=1,**changes):
        return dict(dict(runtime_trait_id=identity,base_limit=1,additional_limit=0,
            used_count=0,trait_type=35,action_timing=6),**changes)
    def group(self,traits,all_required=False):
        return dict(values=traits,enabled_internal=True,enabled_while_all_traits_usable=all_required)
    def unit(self):return dict(id='a',runtime_trait_effects={},records=[],hp=100,max_hp=100,en=40,max_en=40,mp=6)
    def register(self,unit,trait,value=100,can_remove=True):
        record=dict(runtime_trait_id=trait['runtime_trait_id'],PowerValue=value);unit['records'].append(record)
        unit['runtime_trait_effects'][trait['runtime_trait_id']]=dict(
            can_remove=lambda t,u:can_remove,remove=lambda t,u:u['records'].remove(record))
    def test_weapon_used_exhaustion_removes_effect_from_subsequent_power_calculation(self):
        unit=self.unit();trait=self.trait();group=self.group([trait]);self.register(unit,trait)
        weapon=dict(map_level_stats=dict(power=1000,hit=100,critical=0,hp_recovery_rate=0,en_recovery_rate=0,mp_recovery_value=0))
        self.assertEqual(properties(unit,weapon,unit['records'])['power'],1100)
        result=consume_resolved_set(group,[1],dict(turn=1,id=0),6,[unit])
        self.assertEqual(properties(unit,weapon,unit['records'])['power'],1000)
        self.assertTrue(result['remove_set']);self.assertEqual(result['removals'][0]['units'],['a'])
        self.assertEqual(unit['runtime_trait_effects'],{})
    def test_all_usable_group_removes_companion_effects_and_stops_consuming(self):
        unit=self.unit();first=self.trait();second=self.trait(2,base_limit=0)
        for trait in (first,second):self.register(unit,trait)
        result=consume_resolved_set(self.group([first,second],True),[1,2],None,6,[unit])
        self.assertEqual(result['visited'],[1]);self.assertEqual(second['used_count'],0)
        self.assertEqual(unit['records'],[]);self.assertTrue(result['remove_set'])
    def test_independent_group_retains_unexhausted_companion(self):
        unit=self.unit();first=self.trait();second=self.trait(2,base_limit=2)
        for trait in (first,second):self.register(unit,trait)
        result=consume_resolved_set(self.group([first,second]),[1],None,6,[unit])
        self.assertFalse(result['remove_set']);self.assertEqual(set(unit['runtime_trait_effects']),{2})
    def test_special_114_can_deactivate_whole_group_without_being_removable(self):
        unit=self.unit();trait=self.trait(trait_type=114);self.register(unit,trait)
        result=consume_resolved_set(self.group([trait],True),[1],None,6,[unit])
        self.assertEqual(unit['records'],[]);self.assertFalse(result['remove_set'])
    def test_registry_identity_and_gate_prevent_unrelated_removal(self):
        unit=self.unit();trait=self.trait();self.register(unit,trait,can_remove=False)
        self.assertFalse(remove_from_unit(self.trait(2),unit))
        self.assertFalse(remove_from_unit(trait,unit));self.assertEqual(len(unit['records']),1)
        self.assertTrue(remove_from_unit(trait,unit,force=True));self.assertEqual(unit['records'],[])
    def test_disabled_set_skips_consumption_and_empty_set_is_all_removable(self):
        unit=self.unit();trait=self.trait();group=self.group([trait]);group['enabled_internal']=False
        result=consume_resolved_set(group,[1],None,6,[unit])
        self.assertEqual(result['visited'],[]);self.assertEqual(trait['used_count'],0)
        self.assertTrue(is_all_removable(self.group([])))
        self.assertTrue(is_all_removable(self.group(None)))


if __name__=='__main__':unittest.main()
