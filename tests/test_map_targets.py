from copy import deepcopy
from types import SimpleNamespace
import random
import unittest
from battle_headless.battle_map import BattleMap
from battle_headless.occupancy import UnitFootprint
from battle_headless.geometry import MapGeometry,Area,ExcludeArea
from battle_headless.map_targets import target_candidates,select_range_targets,attach_map_targeting,filter_map_range
from battle_headless.battle_sides import is_opposition,is_ally,is_ally_or_friendly,is_alive_on_map

class MapTargetTests(unittest.TestCase):
 def env(self):
  units={str(side):dict(id=str(side),battle_side=side,team='player' if side in (0,1,4,7) else 'enemy',
      position=[side,side],direction=2,hp=100,existence=0) for side in range(8)}
  env=SimpleNamespace(units=units,width=12,height=12,blocked=set(),rng=random.Random(37))
  env.battle_map=BattleMap(env,{key:UnitFootprint(1) for key in units})
  return env
 def test_recovery_uses_allies_not_merely_non_enemies(self):
  env=self.env();actor=env.units['0']
  self.assertEqual([u['id'] for u in target_candidates(env,actor,1)],['2','3','5','6'])
  self.assertEqual([u['id'] for u in target_candidates(env,actor,2)],['0','1'])
  self.assertTrue(is_ally_or_friendly(0,4));self.assertFalse(is_ally(0,4))
  coords=[(n,n) for n in range(8)]
  self.assertEqual([t['target'] for t in select_range_targets(env,actor,coords,2)],['0','1'])
 def test_footprint_intersection_deduplication_order_and_origin(self):
  env=self.env();actor=env.units['0']
  env.units={key:env.units[key] for key in ('0','5','2')}
  env.units['2']['position']=[2,3];env.units['5']['position']=[9,9]
  env.battle_map.footprints['2']=UnitFootprint(2)
  targets=select_range_targets(env,actor,[(3,4),(2,3),(9,9),(3,4)],1)
  self.assertEqual([t['target'] for t in targets],['2','5'])
  self.assertEqual(targets[0],dict(target='2',target_coord=[2,3],first_effect_coord=[3,4],hit_count=1))
  self.assertEqual([t['target'] for t in select_range_targets(env,actor,[(9,9),(3,4)],1)],['5','2'])
 def test_original_existence_controls_repository_membership(self):
  env=self.env();actor=env.units['0'];env.units['2'].update(hp=0,existence=0)
  env.units['3']['existence']=5;env.units['5']['existence']=2;env.units['6']['existence']=1
  self.assertEqual([u['id'] for u in target_candidates(env,actor,1)],['2','6'])
  self.assertTrue(is_alive_on_map(0));self.assertTrue(is_alive_on_map(1))
  for value in (-1,2,5,6,7,8,9,11,255):self.assertFalse(is_alive_on_map(value))
 def test_closed_map_cells_filter_and_selection_are_pure(self):
  env=self.env();env.battle_map.geometry=MapGeometry(Area(0,0,12,12),(ExcludeArea(1,Area(2,2,1,1)),))
  before=deepcopy(env.units);rng=env.rng.getstate()
  coords=filter_map_range(env,[(-1,0),(2,2),(3,3),(99,99),(3,3)])
  self.assertEqual(coords,[(3,3)])
  result=select_range_targets(env,env.units['0'],coords,1)
  self.assertEqual([t['target'] for t in result],['3'])
  self.assertEqual(env.units,before);self.assertEqual(env.rng.getstate(),rng)
  # Target lookup itself does not invent an extra existence filter: callers
  # choose the original range service before supplying its effect coordinates.
  self.assertEqual([t['target'] for t in select_range_targets(env,env.units['0'],[(2,2)],1)],['2'])
 def test_explicit_sides_override_team_names_and_existence_fallbacks(self):
  env=self.env();actor=env.units['0'];env.units['2']['team']='player'
  self.assertIn(env.units['2'],target_candidates(env,actor,1))
  env.units['2'].pop('existence');env.units['2']['chance_step']=dict(existence=6)
  self.assertNotIn(env.units['2'],target_candidates(env,actor,1))
  env.units['2'].pop('chance_step');env.units['2'].update(hp=0,zero_hp_survival=True)
  self.assertIn(env.units['2'],target_candidates(env,actor,1))
 def test_everyone_ally_and_invalid_source_semantics(self):
  env=self.env();actor=env.units['7']
  self.assertEqual(target_candidates(env,actor,1),[])
  self.assertEqual([u['id'] for u in target_candidates(env,actor,2)],[str(i) for i in range(7)])
  self.assertTrue(is_ally(7,255));self.assertFalse(is_opposition(7,255))
  self.assertFalse(is_ally(0,255));self.assertTrue(is_ally_or_friendly(0,255))
  with self.assertRaises(ValueError):is_opposition(255,0)
 def test_core_targeting_callback_is_pure_and_uses_weapon_category(self):
  env=self.env();attach_map_targeting(env);before=deepcopy(env.units);rng=env.rng.getstate()
  self.assertEqual([t['target'] for t in env.map_weapon_targets(env.units['0'],dict(map_trait_category=1),[(2,2),(3,3)])],['2','3'])
  self.assertEqual(env.units,before);self.assertEqual(env.rng.getstate(),rng)
  self.assertEqual(env.map_target_profile,'native-range-and-whole-map-target-subset')

if __name__=='__main__':unittest.main()
