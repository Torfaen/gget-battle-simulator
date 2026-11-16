from dataclasses import replace
import unittest
from battle_headless.geometry import Area,ExcludeArea,MapGeometry,effective_places


class MapExistenceTests(unittest.TestCase):
    def test_inclusive_map_edges_and_outside(self):
        geometry=MapGeometry(Area(-2,3,4,5))
        for coord in [(-2,3),(1,7)]:self.assertTrue(geometry.exists(coord))
        for coord in [(-3,3),(2,7),(-2,2),(1,8)]:self.assertFalse(geometry.exists(coord))

    def test_open_exclusion_does_not_reopen_overlapping_closed_area(self):
        first=ExcludeArea(1,Area(1,1,3,3),True)
        second=ExcludeArea(2,Area(2,2,3,3),False)
        geometry=MapGeometry(Area(0,0,10,10),(first,second))
        self.assertTrue(geometry.exists((1,1)))
        self.assertFalse(geometry.exists((2,2)))
        self.assertTrue(replace(geometry,exclusions=(first,replace(second,opened=True))).exists((2,2)))

    def test_open_exclusion_never_extends_map_bounds(self):
        geometry=MapGeometry(Area(0,0,3,3),(ExcludeArea(1,Area(2,2,4,4),True),))
        self.assertTrue(geometry.exists((2,2)))
        self.assertFalse(geometry.exists((3,3)))

    def test_normal_weapon_range_respects_closed_areas(self):
        geometry=MapGeometry(Area(0,0,5,5),(ExcludeArea(1,Area(2,0,1,3)),))
        places=effective_places(Area(0,0),1,4,geometry)
        self.assertIn((1,0),places)
        self.assertNotIn((2,0),places)
        self.assertNotIn((-1,0),places)

    def test_serialized_initial_area_is_preserved(self):
        self.assertEqual(MapGeometry.from_dimensions(24,28).bounds,Area(0,0,24,28))
        initial=Area(4,5,6,7)
        geometry=MapGeometry.from_dimensions(24,28,initial_area=initial)
        self.assertEqual(geometry.bounds,initial)
        self.assertFalse(geometry.exists((0,0)))
        self.assertTrue(geometry.exists((4,5)))


if __name__=='__main__':unittest.main()
