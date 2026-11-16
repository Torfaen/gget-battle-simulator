import unittest
from battle_headless.battle_resources import begin_battle,end_battle,reflect_en_change,mp_change_info
from battle_headless.weapon_use import reflect_mp_change


class BattleResourceTests(unittest.TestCase):
    def unit(self):return dict(hp=90,max_hp=100,en=40,max_en=100,mp=10)
    def test_start_resets_reflected_mp_but_keeps_pending_queues(self):
        unit=self.unit();unit.update(pending_mp_change=5,pending_en_change=-20,mp_change_since_battle_start=99)
        begin_battle(unit)
        self.assertEqual((unit['battle_start_hp'],unit['battle_start_en'],unit['battle_start_mp']),(90,40,10))
        self.assertEqual((unit['pending_mp_change'],unit['pending_en_change'],unit['mp_change_since_battle_start']),(5,-20,0))
        self.assertTrue(unit['has_participated_in_battle'])
    def test_end_clears_hp_en_only_and_info_retains_clamp_difference(self):
        unit=self.unit();begin_battle(unit);unit['pending_mp_change']=5;end_battle(unit)
        self.assertNotIn('battle_start_hp',unit);self.assertNotIn('battle_start_en',unit)
        self.assertEqual(mp_change_info(unit),dict(requested_change=5,effective_change=2))
        reflect_mp_change(unit)
        self.assertEqual(mp_change_info(unit),dict(requested_change=5,effective_change=2))
        unit['pending_mp_change']=-20
        self.assertEqual(mp_change_info(unit),dict(requested_change=-15,effective_change=-10))
    def test_en_queue_applies_once_and_clamps_independently_of_immediate_en(self):
        unit=self.unit();unit['pending_en_change']=-70
        self.assertEqual(reflect_en_change(unit),-40);self.assertEqual(unit['en'],0)
        self.assertEqual(unit['pending_en_change'],0);self.assertEqual(reflect_en_change(unit),0)
        unit['pending_en_change']=500;self.assertEqual(reflect_en_change(unit),100)
        self.assertEqual(unit['en'],100)
    def test_zero_en_queue_does_not_reclamp_an_over_cap_current_value(self):
        unit=self.unit();unit['en']=120
        self.assertEqual(reflect_en_change(unit),0);self.assertEqual(unit['en'],120)
    def test_mp_change_query_requires_captured_baseline(self):
        with self.assertRaises(KeyError):mp_change_info(self.unit())


if __name__=='__main__':unittest.main()
