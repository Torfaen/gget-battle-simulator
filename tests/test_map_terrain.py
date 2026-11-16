from types import SimpleNamespace
import unittest
from battle_headless.map_terrain import chip_terrain_data,can_stay,MapTerrainRuntime,moving_check
from battle_headless.geometry import MapGeometry
from battle_headless.occupancy import UnitFootprint


class MapTerrainTests(unittest.TestCase):
    def capability(self,order=(2,4,5,6),**changes):
        result=dict(sorted_standby_terrains=list(order),capabilities={kind:3 for kind in range(2,7)})
        result['capabilities'].update(changes);return result
    def test_empty_preference_blocks_even_existing_selected_type(self):
        self.assertIsNone(chip_terrain_data({2:'space'},[],2))
        self.assertIsNone(chip_terrain_data({2:'space'},None,2))
    def test_selected_type_precedes_preference_and_preserves_present_null(self):
        self.assertEqual(chip_terrain_data({2:'space',4:'ground'},[4,2],2),'space')
        self.assertIsNone(chip_terrain_data({2:None,4:'ground'},[4,2],2))
    def test_fallback_only_ground_surface_underwater_in_original_order(self):
        chip={2:'space',3:'air',4:'ground',5:'water',6:'underwater'}
        self.assertEqual(chip_terrain_data(chip,[2,3,6,4,5],0),'underwater')
        self.assertIsNone(chip_terrain_data(chip,[2,3],0))
    def test_can_stay_only_rejects_incapable_enum_one_and_all_uses_first(self):
        for value,expected in ((0,True),(1,False),(2,True),(3,True),(-1,True)):
            cap=self.capability();cap['capabilities'][2]=value
            self.assertEqual(can_stay(cap,2),expected);self.assertEqual(can_stay(cap,1),expected)
        self.assertFalse(can_stay(self.capability(order=()),1));self.assertFalse(can_stay(self.capability(),0))
    def test_wall_missing_coordinate_chip_terrain_or_incapability(self):
        cap=self.capability();runtime=MapTerrainRuntime({(1,1):{2:dict(TerrainTypeIndex=2)}},MapGeometry.from_dimensions(4,4))
        self.assertFalse(runtime.contain_wall((1,1),2,cap))
        self.assertTrue(runtime.contain_wall((-1,1),2,cap));self.assertTrue(runtime.contain_wall((2,2),2,cap))
        self.assertTrue(runtime.contain_wall((1,1),0,cap))
        cap['capabilities'][2]=1;self.assertTrue(runtime.contain_wall((1,1),2,cap))
    def test_empty_wall_list_and_short_circuit(self):
        runtime=MapTerrainRuntime({},MapGeometry.from_dimensions(4,4));cap=self.capability()
        self.assertFalse(runtime.contain_walls([],2,cap))
        def places():yield (-1,0);self.fail('Must stop at first wall')
        self.assertTrue(runtime.contain_walls(places(),2,cap))
    def test_core_connects_destination_chips_and_occupancy_without_callbacks(self):
        geometry=MapGeometry.from_dimensions(10,10);coords=((2,3),(2,5),(2,6))
        stage=SimpleNamespace(terrain={c:[dict(TerrainTypeIndex=2)] for c in coords})
        env=SimpleNamespace(_map_context_stage=stage,battle_map=SimpleNamespace(geometry=geometry,footprints={'actor':UnitFootprint(1)}))
        unit=dict(id='actor',direction=2,position=[2,2]);weapon=dict(map_range_type=4,shooting_range='(0,1)(0,3)')
        context=dict(origin=(2,2),standby_terrain_type=2,terrain_capability=self.capability(),occupancy={(2,3):'enemy',(2,6):'actor'})
        result=moving_check(env,unit,weapon,**context)
        self.assertEqual(result['destination'],[2,6]);self.assertTrue(result['available_after_check'])
        values={kind:(3 if kind==2 else 1) for kind in range(2,7)}
        unit['terrain_capability_sources']=dict(base_values=dict(values),upgraded_values=dict(values),corrections=[])
        context.pop('terrain_capability')
        self.assertEqual(moving_check(env,unit,weapon,**context),result)
        del unit['terrain_capability_sources']['upgraded_values']
        unit['terrain_capability_sources']['option_parts_changes']=[]
        self.assertEqual(moving_check(env,unit,weapon,**context),result)
        context['occupancy'][(2,6)]='other';self.assertEqual(moving_check(env,unit,weapon,**context)['reasons'],[10])
        self.assertEqual(unit['position'],[2,2]);self.assertFalse(result['complete_action_eligibility'])
    def test_overlapping_same_type_motifs_require_resolved_chip(self):
        stage=SimpleNamespace(terrain={(1,1):[dict(TerrainTypeIndex=2),dict(TerrainTypeIndex=2)]})
        with self.assertRaises(NotImplementedError):MapTerrainRuntime.from_stage(stage,MapGeometry.from_dimensions(4,4))


if __name__=='__main__':unittest.main()
