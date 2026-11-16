import unittest
from battle_headless.geometry import Area,around_range,effective_places,target_within_range


class GeometryTests(unittest.TestCase):
    def test_rectangle_expands_from_all_edges(self):
        area=Area(4,5,2,3)
        places=around_range(area,1,1)
        self.assertIn((6,6),places)
        self.assertIn((3,7),places)
        self.assertIn((5,8),places)
        self.assertNotIn((4,5),places)
        self.assertNotIn((6,8),places)

    def test_minimum_range_and_corner_option(self):
        point=Area(0,0)
        self.assertEqual(around_range(point,0,0),{(0,0)})
        self.assertEqual(around_range(point,3,2),set())
        self.assertIn((1,1),around_range(point,2,2))
        self.assertNotIn((1,0),around_range(point,2,2))
        self.assertNotIn((1,1),around_range(point,2,2,False))
        self.assertIn((2,0),around_range(point,2,2,False))

    def test_large_target_intersection_is_not_pivot_distance(self):
        existing={(x,z) for x in range(10) for z in range(10)}
        attacker=Area(0,0);target=Area(2,0,3,2)
        self.assertTrue(target_within_range(attacker,target,3,3,existing))
        self.assertNotIn((2,0),effective_places(attacker,3,3,existing))
        self.assertFalse(target_within_range(attacker,target,6,6,existing))

    def test_map_clipping_uses_supplied_existing_places(self):
        existing={(0,0),(1,0),(2,0)}
        self.assertEqual(effective_places(Area(0,0),1,3,existing),{(1,0),(2,0)})
        self.assertFalse(target_within_range(Area(0,0),Area(-1,0),1,1,existing))

    def test_irregular_target_does_not_fill_its_bounding_box(self):
        target={(2,0),(3,1)}
        self.assertEqual(Area.from_coords(target),Area(2,0,2,2))
        self.assertFalse(target_within_range(Area(1,1),target,1,1,{(2,1)}))
        self.assertTrue(target_within_range(Area(1,1),Area.from_coords(target),1,1,{(2,1)}))


if __name__=='__main__':unittest.main()
