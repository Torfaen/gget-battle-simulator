import unittest
from battle_headless.map_target_sort import sort_results,spiral_compare,directed_compare,service_sort_parameters,service_sort_results


class MapTargetSortTests(unittest.TestCase):
    def rows(self,coords):return [dict(target=str(n),target_coord=list(p)) for n,p in enumerate(coords)]
    def test_spiral_radius_then_clockwise_from_up(self):
        coords=[(0,-1),(1,0),(0,1),(-1,0),(0,0),(0,2)]
        rows=self.rows(coords)
        self.assertEqual([r['target_coord'] for r in sort_results(rows,1,(0,0))],
            [[0,0],[0,1],[1,0],[0,-1],[-1,0],[0,2]])
        self.assertEqual([r['target_coord'] for r in rows],[list(p) for p in coords])
    def test_direction_compare_uses_native_axis_order(self):
        a=(1,2);b=(2,1)
        self.assertEqual([directed_compare(a,b,(10,10),d) for d in (1,2,3,4,0,5)],[-1,1,1,-1,0,0])
    def test_origin_translation_and_int32_overflow(self):
        self.assertEqual(spiral_compare((6,6),(5,6),(5,5)),1)
        self.assertEqual(spiral_compare((-2147483648,0),(0,0),(0,0)),-1)
    def test_same_coordinate_ties_use_reference_sort_not_stable_sort(self):
        rows=self.rows([(1,1)]*40)
        ordered=sort_results(rows,1,(0,0))
        self.assertEqual(sorted(r['target'] for r in ordered),sorted(r['target'] for r in rows))
        self.assertNotEqual([r['target'] for r in ordered],[r['target'] for r in rows])
    def test_unrecognized_sort_is_native_no_sort(self):
        rows=self.rows([(2,1),(0,0)])
        self.assertEqual(sort_results(rows,0,(0,0)),rows)
        with self.assertRaises(NotImplementedError):sort_results([dict(target='x')],1,(0,0))

    def test_service_range_policy_and_single_or_multi_origin(self):
        for kind,mode in enumerate((1,1,2,2,1,2),1):
            weapon=dict(map_range_type=kind,impact_point_max_select_count=1)
            policy=service_sort_parameters(weapon,[(4,5),(6,7)],direction=3)
            self.assertEqual(policy,dict(target_sort_type=mode,origin=[4,5],direction=3))
        weapon=dict(map_range_type=2,impact_point_max_select_count=2)
        with self.assertRaises(NotImplementedError):service_sort_parameters(weapon,[(4,5)],direction=2)
        self.assertEqual(service_sort_parameters(weapon,[(4,5)],direction=2,step_free_move_coord=(8,9))['origin'],[8,9])
        self.assertEqual(service_sort_parameters(dict(map_range_type=5),[],direction=2)['origin'],[0,0])
    def test_service_applies_second_reference_sort_to_shared_dto_order(self):
        rows=self.rows([(1,1)]*40);policy=dict(target_sort_type=1,origin=[0,0],direction=2)
        once=sort_results(rows,1,(0,0));twice=service_sort_results(rows,policy)
        self.assertNotEqual([r['target'] for r in once],[r['target'] for r in twice])


if __name__=='__main__':unittest.main()
