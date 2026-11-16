import unittest
from types import SimpleNamespace
from battle_headless.map_destination import destination_coord,move_destination
from battle_headless.occupancy import UnitFootprint


class MapDestinationTests(unittest.TestCase):
    def test_four_cardinal_directions_use_directional_shooting_offsets(self):
        for direction,expected in ((1,(14,20)),(2,(10,24)),(3,(6,20)),(4,(10,16))):
            self.assertEqual(destination_coord((10,20),[(0,0)],[(0,1),(0,3)],direction),expected)
    def test_asymmetric_footprint_uses_both_bounds(self):
        occupied=[(-2,0),(0,0),(3,0)]
        self.assertEqual(destination_coord((10,20),occupied,[(0,3)],1),(15,20))
        self.assertEqual(destination_coord((10,20),occupied,[(0,3)],3),(1,20))
    def test_origin_addition_wraps_int32(self):
        self.assertEqual(destination_coord((2147483647,20),[(0,0)],[(0,1)],1),(-2147483647,20))
    def test_empty_or_unknown_direction_explicitly_rejected(self):
        for direction in (0,5):
            with self.assertRaises(ValueError):destination_coord((0,0),[(0,0)],[(0,1)],direction)
        with self.assertRaises(ValueError):destination_coord((0,0),[(0,0)],[],2)
        with self.assertRaises(ValueError):destination_coord((0,0),[],[(0,1)],2)
    def test_overflowing_range_comparison_not_silently_replaced_with_extrema(self):
        with self.assertRaises(NotImplementedError):destination_coord((0,0),[(0,0)],[(0,-2147483648),(0,1)],2)
    def test_core_adapter_requires_explicit_step_origin_and_uses_real_footprint(self):
        footprint=UnitFootprint(2,front=1,rear=2)
        env=SimpleNamespace(battle_map=SimpleNamespace(footprints={'actor':footprint}))
        unit=dict(id='actor',direction=2,position=[99,99]);weapon=dict(map_range_type=4,shooting_range='(0,2)')
        result=move_destination(env,unit,weapon,origin=(3,4))
        self.assertEqual(result,(3,7));self.assertEqual(unit['position'],[99,99])
        with self.assertRaises(ValueError):move_destination(env,unit,dict(weapon,map_range_type=3),origin=(3,4))


if __name__=='__main__':unittest.main()
