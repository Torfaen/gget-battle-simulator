import unittest
from battle_headless.map_moving import can_standby_places,is_immovable,moving_after_check


class MovingMapTests(unittest.TestCase):
    def test_destination_wall_short_circuits_occupancy_and_shooting(self):
        calls=[]
        def walls(places,terrain,traits):calls.append((places,terrain,traits));return True
        def occupants(places):self.fail('Occupancy must not be queried behind a wall')
        result=moving_after_check('actor',[(3,4)],[(1,2)],2,'traits',contain_walls=walls,find_unit_ids=occupants)
        self.assertEqual(tuple(map(int,result)),(10,));self.assertEqual(calls,[([(3,4)],2,'traits')])
    def test_destination_accepts_empty_or_only_self_not_another_unit(self):
        for occupants,expected in ((None,True),([],True),(['actor'],True),(['actor','actor'],True),(['enemy'],False),(['actor','ally'],False)):
            self.assertEqual(can_standby_places('actor',[(0,0)],2,None,
                contain_walls=lambda *args:False,find_unit_ids=lambda places:occupants),expected)
    def test_occupied_destination_short_circuits_shooting_wall_test(self):
        calls=[]
        def walls(places,*args):calls.append(places);return False
        result=moving_after_check('actor',[(2,2)],[(1,1)],2,None,contain_walls=walls,find_unit_ids=lambda places:['other'])
        self.assertEqual(tuple(map(int,result)),(10,));self.assertEqual(calls,[[(2,2)]])
    def test_shooting_places_unique_first_order_and_wall_inputs_preserved(self):
        calls=[]
        def walls(places,terrain,traits):calls.append((places,terrain,traits));return len(calls)==2
        result=moving_after_check('actor',[(2,2)],[(1,2),(1,2),(1,3)],6,['opaque-wall-trait'],
            contain_walls=walls,find_unit_ids=lambda places:[])
        self.assertEqual(tuple(map(int,result)),(10,))
        self.assertEqual(calls[1],([(1,2),(1,3)],6,['opaque-wall-trait']))
    def test_clear_destination_and_shooting_pass(self):
        self.assertEqual(moving_after_check('actor',[(2,2)],[],2,None,
            contain_walls=lambda *args:False,find_unit_ids=lambda places:['actor']),())
    def test_immovable_service_preserves_destination_identity_and_order(self):
        destination=object();calls=[]
        def standby(places):self.assertIs(places,destination);calls.append('standby');return True
        def walls(*args):calls.append('walls');return False
        self.assertFalse(is_immovable(destination,[(1,2)],2,None,can_standby=standby,contain_walls=walls))
        self.assertEqual(calls,['standby','walls'])


if __name__=='__main__':unittest.main()
