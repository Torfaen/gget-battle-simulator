from copy import deepcopy
import unittest
import test_map_targets as targeting
from battle_headless.map_result_runtime import hit_value,result_operations,recovery_result,apply_result
from battle_headless.map_targets import attach_map_targeting
from battle_headless.hp_barrier import register_barrier
from battle_headless.unit_life import is_alive

class MapResultRuntimeTests(unittest.TestCase):
 def unit(self):return dict(hp=40,max_hp=100,en=10,max_en=50,mp=6,existence=0)
 def test_nullable_getter_miss_keeps_present_zero_and_absent_none(self):
  result=dict(hit=False,hp_damage=20,en_damage=None,mp_damage=0)
  self.assertEqual(hit_value(result,'hp_damage'),0)
  self.assertIsNone(hit_value(result,'en_damage'))
  self.assertEqual(hit_value(result,'mp_damage'),0)
 def test_recovery_negative_damage_encoding_ceil_caps_and_deferred_mp(self):
  unit=self.unit();unit.update(max_hp=101,max_en=21,en=20)
  result=recovery_result(unit,hp_rate=5,en_rate=10,mp_value=2)
  self.assertEqual((result['hp_damage'],result['en_damage'],result['mp_damage']),(-6,-3,-2))
  applied=apply_result(unit,result)
  self.assertEqual((unit['hp'],unit['en'],unit['mp'],unit['pending_mp_change']),(46,21,6,2))
  self.assertEqual(applied['pending_lifecycle'],[])
 def test_attack_damage_barrier_removal_unbreakable_and_pending_damage_cut(self):
  unit=self.unit();unit['scalar_corrections']={'UnbreakableCount':1}
  register_barrier(unit,'shield',100,20)
  result=dict(hit=True,is_attack=True,hp_damage=100,hp_barrier_damage=20,en_damage=5,mp_damage=2)
  applied=apply_result(unit,result)
  self.assertEqual((unit['hp'],unit['hp_barrier'],unit['en'],unit['pending_mp_change']),(0,0,5,-2))
  self.assertTrue(is_alive(unit));self.assertEqual(unit['existence'],0)
  self.assertEqual(unit['hp_barriers'],[])
  self.assertEqual(applied['pending_lifecycle'],['remove_damage_cut'])
 def test_prediction_mutates_shadow_and_preserves_actual_state(self):
  unit=self.unit();register_barrier(unit,'shield',100,20);before=deepcopy(unit)
  result=dict(hit=True,is_attack=True,hp_damage=100,hp_barrier_damage=20,en_damage=5,mp_damage=2)
  applied=apply_result(unit,result,prediction=True)
  self.assertEqual(unit,before)
  self.assertEqual((applied['after']['hp'],applied['after']['en'],applied['after']['pending_mp_change']),(0,5,-2))
  self.assertEqual(applied['after']['existence'],2)
  self.assertEqual(len(applied['after']['hp_barriers']),1)
  self.assertNotIn(('remove_damage_barrier',),applied['operations'])
 def test_miss_routes_evasion_without_applying_resource_damage(self):
  unit=self.unit();before=deepcopy(unit)
  result=dict(hit=False,is_attack=True,hp_damage=100,en_damage=5,mp_damage=2)
  applied=apply_result(unit,result)
  self.assertEqual(unit,before);self.assertEqual(applied['operations'],[('attack_evaded',)])
  self.assertEqual(applied['pending_lifecycle'],['attack_evaded'])
  apply_result(unit,result,lifecycle={'attack_evaded':lambda u:u.update(evasion_callback=True)})
  self.assertTrue(unit['evasion_callback'])
 def test_mp_disable_is_present_zero_in_recovery_and_never_queues_value(self):
  unit=self.unit();unit['scalar_corrections']={'MpLimitBreakCount':1}
  result=recovery_result(unit,hp_rate=0,en_rate=0,mp_value=2)
  self.assertEqual(result['mp_damage'],0)
  self.assertIsNone(recovery_result(unit,hp_rate=0,en_rate=0,mp_value=None)['mp_damage'])
  apply_result(unit,result);self.assertEqual(unit['pending_mp_change'],0)
 def test_core_recovery_does_not_multiply_repeated_impact_count_or_pay_fees(self):
  env=targeting.MapTargetTests().env();env.units={key:env.units[key] for key in ('0','1')}
  for unit in env.units.values():unit.update(self.unit())
  env.units['1']['position']=[3,1];attach_map_targeting(env)
  weapon=dict(map_range_type=2,map_trait_category=2,effect_range='(0,0)',hp_recovery_rate=20,en_recovery_rate=20,mp_recovery_value=2)
  before=deepcopy(env.units);rng=env.rng.getstate()
  predicted=env.map_weapon_recover(env.units['0'],weapon,[(3,1),(3,1)],True)
  self.assertEqual(env.units,before)
  self.assertEqual(predicted['results'][0]['application']['after']['hp'],60)
  actual=env.map_weapon_recover(env.units['0'],weapon,[(3,1),(3,1)])
  self.assertEqual(actual['results'][0]['hit_count'],2)
  self.assertEqual((env.units['1']['hp'],env.units['1']['en'],env.units['1']['pending_mp_change']),(60,20,2))
  self.assertFalse(actual['fees_applied']);self.assertFalse(actual['action_advanced'])
  self.assertEqual(env.units['0'],before['0']);self.assertEqual(env.rng.getstate(),rng)
 def test_escape_requires_unported_routing_before_any_mutation(self):
  unit=self.unit();before=deepcopy(unit)
  with self.assertRaises(NotImplementedError):apply_result(unit,dict(hit=True,is_attack=True,hp_damage=100),can_escape=True)
  self.assertEqual(unit,before)

if __name__=='__main__':unittest.main()
