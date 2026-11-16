import unittest
from battle_headless.terrain_capability import from_values,compose,compare_preference


class TerrainCapabilityTests(unittest.TestCase):
    def values(self,value=3):return {kind:value for kind in range(2,7)}
    def test_native_equal_value_priority_is_space_ground_atmosphere_surface_underwater(self):
        self.assertEqual(from_values(self.values())['sorted_standby_terrains'],[2,4,3,5,6])
    def test_filters_only_incapable_and_prefers_higher_values(self):
        values={2:0,3:1,4:2,5:3,6:0}
        self.assertEqual(from_values(values)['sorted_standby_terrains'],[5,4,2,6])
    def test_empty_registered_corrections_uses_upgraded_not_base(self):
        self.assertEqual(compose(self.values(1),self.values(3),[])['sorted_standby_terrains'],[2,4,3,5,6])
    def test_matching_is_against_base_not_previous_result_and_last_match_wins(self):
        rows=[dict(target_terrain_type=2,from_capability=1,to_capability=2),
            dict(target_terrain_type=2,from_capability=2,to_capability=3),
            dict(target_terrain_type=2,from_capability=1,to_capability=0)]
        result=compose(self.values(1),self.values(3),rows)
        self.assertEqual(result['capabilities'][2],0);self.assertEqual(result['sorted_standby_terrains'],[2])
    def test_nonempty_nonmatching_sources_still_select_base_branch(self):
        rows=[dict(target_terrain_type=1,from_capability=1,to_capability=3)]
        self.assertEqual(compose(self.values(1),self.values(3),rows)['sorted_standby_terrains'],[])
    def test_extreme_comparator_wrap_is_preserved_but_full_overflow_sort_rejected(self):
        self.assertEqual(compare_preference((2,-2147483648),(4,2147483647)),-1)
        values=self.values();values.update({2:-2147483648,4:2147483647})
        with self.assertRaises(NotImplementedError):from_values(values)


if __name__=='__main__':unittest.main()
