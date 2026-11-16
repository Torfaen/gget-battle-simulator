from copy import deepcopy
import unittest
import test_map_targets as targeting
from battle_headless.map_targets import attach_map_targeting
from battle_headless.occupancy import UnitFootprint
from battle_headless.geometry import MapGeometry,Area,ExcludeArea

class MapExecutionPlanTests(unittest.TestCase):
 def fixture(self):
  env=targeting.MapTargetTests().env();env.units={key:env.units[key] for key in ('0','2','5')}
  env.units['2']['position']=[3,1];env.units['5']['position']=[9,1]
  env.battle_map.footprints['2']=UnitFootprint(2)
  attach_map_targeting(env)
  return env,dict(map_range_type=2,map_trait_category=1,effect_range='(0,0)(1,0)(0,1)(1,1)(0,0)')
 def test_per_impact_dedup_then_repeated_impact_count_and_single_target_entry(self):
  env,weapon=self.fixture();before=deepcopy(env.units);rng=env.rng.getstate()
  impacts=[[3,1],[8,1],[3,1]]
  result=env.map_weapon_plan(env.units['0'],weapon,impacts)
  self.assertEqual([(t['target'],t['hit_count']) for t in result['targets']],[('2',2),('5',1)])
  self.assertEqual(result['targets'][0]['target_coord'],[3,1])
  self.assertEqual(len(result['effect_ranges']),3)
  self.assertEqual(len(result['effect_ranges'][0]['effect_coords']),4)
  self.assertEqual(impacts,[[3,1],[8,1],[3,1]])
  self.assertEqual(env.units,before);self.assertEqual(env.rng.getstate(),rng)
 def test_final_target_order_is_first_hit_across_impacts(self):
  env,weapon=self.fixture()
  result=env.map_weapon_plan(env.units['0'],weapon,iter([(9,1),(3,1),(9,1)]))
  self.assertEqual([(t['target'],t['hit_count']) for t in result['targets']],[('5',2),('2',1)])
 def test_impact_executor_uses_raw_translation_separately_from_preview_filter(self):
  env,weapon=self.fixture();weapon['effect_range']='(0,0)'
  env.battle_map.geometry=MapGeometry(Area(0,0,12,12),(ExcludeArea(1,Area(3,1)),))
  self.assertEqual(env.map_weapon_scope(env.units['0'],weapon,[3,1])['targets'],[])
  result=env.map_weapon_plan(env.units['0'],weapon,[(3,1)])
  self.assertEqual(result['effect_ranges'][0]['effect_coords'],[[3,1]])
  self.assertEqual([t['target'] for t in result['targets']],['2'])
 def test_nonimpact_range_uses_only_first_origin(self):
  env,weapon=self.fixture();weapon.update(map_range_type=1,effect_range='(0,0)')
  result=env.map_weapon_plan(env.units['0'],weapon,[(3,1),(9,1)])
  self.assertEqual(result['effect_ranges'],[dict(impact_coord=[3,1],effect_coords=[[3,1]])])
  self.assertEqual([t['target'] for t in result['targets']],['2'])
  with self.assertRaises(ValueError):env.map_weapon_plan(env.units['0'],weapon,[])
  with self.assertRaises(ValueError):env.map_weapon_plan(env.units['0'],dict(weapon,map_range_type=2),[])
 def test_fixed_and_whole_map_ignore_selected_impact_collection(self):
  env,weapon=self.fixture()
  def unused():
   raise AssertionError('This executor must not enumerate the supplied impacts')
   yield
  fixed=dict(weapon,map_range_type=6,effect_range='(0,0)',shooting_range='(9,1)',fixed_shooting_direction=2)
  result=env.map_weapon_plan(env.units['0'],fixed,unused())
  self.assertEqual([t['target'] for t in result['targets']],['5'])
  self.assertIsNone(result['effect_ranges'][0]['impact_coord'])
  whole=env.map_weapon_plan(env.units['0'],dict(map_range_type=5,map_trait_category=1),unused())
  self.assertIsNone(whole['effect_ranges'])
  self.assertEqual([t['target'] for t in whole['targets']],['2','5'])
 def test_recovery_counts_allied_hits_and_retains_zero_hp_alive_state(self):
  env,weapon=self.fixture();env.units['2'].update(battle_side=1,hp=0,existence=0)
  weapon.update(map_trait_category=2,effect_range='(0,0)')
  result=env.map_weapon_plan(env.units['0'],weapon,[(3,1),(3,1),(9,1),(0,0)])
  self.assertEqual([(t['target'],t['hit_count']) for t in result['targets']],[('2',2),('0',1)])

if __name__=='__main__':unittest.main()
