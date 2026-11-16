from copy import deepcopy
import unittest
import test_map_targets as targeting
from battle_headless.map_ranges import parse_custom_range,translate_range,around_self_range,direction_coord,directional_range,fixed_position_range
from battle_headless.map_targets import attach_map_targeting
from battle_headless.occupancy import UnitFootprint
from battle_headless.geometry import MapGeometry,Area,ExcludeArea

class MapRangeTests(unittest.TestCase):
 def test_parser_keeps_native_regex_and_ignored_tryparse_failures(self):
  text='junk(1,2)(1,2)(,)(--1,3)(2147483648,-2147483649)(+1,2)(1, 2)'
  self.assertEqual(parse_custom_range(text),[(1,2),(0,0),(0,3)])
  self.assertEqual(parse_custom_range(''),[])
  self.assertEqual(parse_custom_range('(0,2)(-0,2)(1,a)'),[(0,2)])
 def test_translation_uses_signed_int32_and_preserves_first_insertion(self):
  self.assertEqual(translate_range([(1,-1),(1,-1),(0,0)],(2**31-1,-(2**31))),
                   [(-(2**31),2**31-1),(2**31-1,-(2**31))])
 def test_self_exclusion_removes_all_occupied_cells_after_union(self):
  self.assertEqual(around_self_range([(0,1),(0,-1)],[(0,0),(0,1)]),[(0,-1),(0,2)])
  self.assertEqual(around_self_range([(0,0),(0,1)],[(0,0),(0,1)]),[(0,0),(0,1),(0,2)])
  self.assertEqual(around_self_range([],[(0,0)]),[])
 def test_pivot_native_order_is_symmetric_pair_insertion(self):
  footprint=UnitFootprint(3,front=1)
  expected=[(0,0),(0,1),(1,0),(0,2),(2,0),(1,1),(1,2),(2,1),(2,2),(0,3),(1,3),(2,3)]
  self.assertEqual(footprint.ordered_coords(),expected)
  self.assertEqual(set(expected),footprint.coords())
  self.assertEqual(UnitFootprint(1,1,1,1,1).ordered_coords(),[(0,0),(0,1),(-1,0),(1,0),(0,-1)])
 def test_core_scope_connects_real_template_geometry_to_targets_without_costs(self):
  env=targeting.MapTargetTests().env();env.units={key:env.units[key] for key in ('0','2')}
  actor=env.units['0'];env.units['2']['position']=[0,3]
  env.battle_map.footprints['0']=UnitFootprint(2)
  env.battle_map.geometry=MapGeometry(Area(0,0,12,12),(ExcludeArea(1,Area(3,0)),))
  attach_map_targeting(env);before=deepcopy(env.units);rng=env.rng.getstate()
  weapon=dict(map_range_type=1,map_trait_category=1,effect_range='(0,2),(2,0)')
  scope=env.map_weapon_scope(actor,weapon)
  self.assertEqual(scope['effect_coords'],[[0,2],[2,0],[0,3],[2,1],[1,2],[1,3],[3,1]])
  self.assertEqual([t['target'] for t in scope['targets']],['2'])
  self.assertEqual(scope['targets'][0]['target_coord'],[0,3])
  self.assertEqual(before,env.units);self.assertEqual(rng,env.rng.getstate())
 def test_around_self_does_not_rotate_template_with_actor_facing(self):
  env=targeting.MapTargetTests().env();env.units={'0':env.units['0']};actor=env.units['0'];actor['direction']=1
  attach_map_targeting(env)
  weapon=dict(map_range_type=1,map_trait_category=1,effect_range='(0,2)')
  self.assertEqual(env.map_weapon_scope(actor,weapon)['effect_coords'],[[0,2]])
  with self.assertRaises(NotImplementedError):env.map_weapon_scope(actor,dict(weapon,map_range_type=0))
 def test_direction_mapping_is_not_generic_clockwise_rotation(self):
  self.assertEqual([direction_coord((2,3),d) for d in range(6)],
                   [(2,3),(3,2),(2,3),(-3,2),(-2,-3),(2,3)])
  self.assertEqual(direction_coord((-2**31,-2**31),4),(-2**31,-2**31))
 def test_directional_width_edge_and_self_exclusion(self):
  occupied=UnitFootprint(2).ordered_coords((3,3))
  self.assertEqual(directional_range([(0,1),(0,-1),(0,0)],occupied,2),
                   [(3,5),(4,5),(3,2),(4,2)])
  self.assertEqual(directional_range([(-1,2),(0,0),(-1,2)],occupied,1),[(6,2),(6,3)])
 def test_zero_offset_on_asymmetric_footprint_preserves_unoccupied_corners(self):
  occupied=UnitFootprint(1,1,1,1,1).ordered_coords()
  self.assertEqual(directional_range([(0,0)],occupied,2),[(-1,-1),(1,-1)])
  with self.assertRaises(ValueError):directional_range([(0,1)],[],2)
 def test_directional_core_scope_uses_supplied_origin_and_direction_purely(self):
  env=targeting.MapTargetTests().env();env.units={key:env.units[key] for key in ('0','2')}
  actor=env.units['0'];actor['position']=[3,3];actor['direction']=1
  env.units['2']['position']=[6,3];env.battle_map.footprints['0']=UnitFootprint(2)
  attach_map_targeting(env);before=deepcopy(env.units);rng=env.rng.getstate()
  weapon=dict(map_range_type=3,map_trait_category=1,effect_range='(-1,2)(0,0)')
  scope=env.map_weapon_scope(actor,weapon)
  self.assertEqual(scope['effect_coords'],[[6,2],[6,3]])
  self.assertEqual([t['target'] for t in scope['targets']],['2'])
  self.assertEqual(env.map_weapon_scope(actor,dict(weapon,map_range_type=4)),scope)
  shifted=env.map_weapon_scope(actor,dict(weapon,effect_range='(0,1)'),[5,5],2)
  self.assertEqual(shifted['effect_coords'],[[5,7],[6,7]])
  self.assertEqual(env.units,before);self.assertEqual(env.rng.getstate(),rng)
 def test_fixed_origin_uses_first_shooting_coordinate_or_zero_default(self):
  self.assertEqual(fixed_position_range([(2,3),(2,3)],[(7,8),(-1,-1)],1),[(10,10)])
  self.assertEqual(fixed_position_range([(2,3)],[],3),[(-3,2)])
  self.assertEqual(fixed_position_range([(1,1)],[(2**31-1,2**31-1)],2),[(-2**31,-2**31)])
 def test_impact_range_requires_explicit_origin_and_does_not_rotate(self):
  env=targeting.MapTargetTests().env();env.units={key:env.units[key] for key in ('0','6')};actor=env.units['0'];actor['direction']=1
  env.battle_map.footprints['0']=UnitFootprint(3)
  attach_map_targeting(env)
  weapon=dict(map_range_type=2,map_trait_category=1,effect_range='(1,2)(0,0)')
  with self.assertRaises(ValueError):env.map_weapon_scope(actor,weapon)
  scope=env.map_weapon_scope(actor,weapon,[6,6],4)
  self.assertEqual(scope['effect_coords'],[[7,8],[6,6]])
  self.assertEqual([t['target'] for t in scope['targets']],['6'])
 def test_fixed_core_scope_ignores_actor_origin_facing_and_selected_impact(self):
  env=targeting.MapTargetTests().env();env.units={key:env.units[key] for key in ('0','2')}
  actor=env.units['0'];actor.update(position=[8,8],direction=4);env.units['2']['position']=[5,6]
  attach_map_targeting(env);before=deepcopy(env.units);rng=env.rng.getstate()
  weapon=dict(map_range_type=6,map_trait_category=1,effect_range='(2,1)(2,1)',
              shooting_range='(4,4)(9,9)',fixed_shooting_direction=1)
  scope=env.map_weapon_scope(actor,weapon,[-100,-100],3)
  self.assertEqual(scope['effect_coords'],[[5,6]])
  self.assertEqual([t['target'] for t in scope['targets']],['2'])
  self.assertEqual(env.units,before);self.assertEqual(env.rng.getstate(),rng)
 def test_fixed_blank_shooting_range_still_applies_map_filter(self):
  env=targeting.MapTargetTests().env();attach_map_targeting(env)
  weapon=dict(map_range_type=6,map_trait_category=1,effect_range='(-1,0)(0,1)',shooting_range='',fixed_shooting_direction=2)
  self.assertEqual(env.map_weapon_scope(env.units['0'],weapon)['effect_coords'],[[0,1]])
 def test_whole_map_scope_uses_repository_order_without_geometry_or_template(self):
  env=targeting.MapTargetTests().env();env.units={key:env.units[key] for key in ('5','0','2','6')}
  env.units['5']['position']=[99,-5];env.units['2']['position']=[99,-5]
  env.units['6']['existence']=5
  attach_map_targeting(env);before=deepcopy(env.units);rng=env.rng.getstate()
  weapon=dict(map_range_type=5,map_trait_category=1)
  scope=env.map_weapon_scope(env.units['0'],weapon)
  self.assertIsNone(scope['effect_coords'])
  self.assertEqual(scope['targets'],[dict(target='5',target_coord=[99,-5],hit_count=1),dict(target='2',target_coord=[99,-5],hit_count=1)])
  self.assertEqual(env.map_weapon_targets(env.units['0'],weapon),scope['targets'])
  self.assertEqual(env.map_weapon_targets(env.units['0'],weapon,[(0,0)]),scope['targets'])
  self.assertEqual(env.units,before);self.assertEqual(env.rng.getstate(),rng)
 def test_whole_map_recovery_uses_allies_including_self_without_everyone_ally(self):
  env=targeting.MapTargetTests().env();env.units={key:env.units[key] for key in ('7','1','4','0','2')}
  attach_map_targeting(env)
  scope=env.map_weapon_scope(env.units['0'],dict(map_range_type=5,map_trait_category=2))
  self.assertEqual([t['target'] for t in scope['targets']],['1','0'])
  with self.assertRaises(ValueError):env.map_weapon_targets(env.units['0'],dict(map_range_type=2,map_trait_category=1))

if __name__=='__main__':unittest.main()
