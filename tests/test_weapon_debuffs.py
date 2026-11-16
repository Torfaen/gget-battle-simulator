from copy import deepcopy
import unittest
from battle_headless.weapon_debuffs import addition_for_group,can_overwrite_trait,higher_priority

class WeaponDebuffAdditionTests(unittest.TestCase):
    def test_overwrite_uses_absolute_strength_then_remaining_duration(self):
        current=dict(base_value=-15,additional_value=-5,limit=3,used_count=2,base_limit=3)
        self.assertFalse(can_overwrite_trait(current,dict(TraitValue=-10,Limit=99)))
        self.assertTrue(can_overwrite_trait(current,dict(TraitValue=-25,Limit=1)))
        self.assertFalse(can_overwrite_trait(current,dict(TraitValue=-20,Limit=1)))
        self.assertTrue(can_overwrite_trait(current,dict(TraitValue=-20,Limit=2)))
        self.assertTrue(can_overwrite_trait(current,dict(TraitValue=-5,Limit=1),stack_count=3,additional_value=-5,additional_limit=1))

    def test_unlimited_ties_and_minimum_int_magnitude(self):
        self.assertFalse(higher_priority(-10,999,True,-10,1,True))
        self.assertTrue(higher_priority(-10,0,True,-10,999,False))
        self.assertFalse(higher_priority(-10,100,False,-10,1,True))
        self.assertTrue(higher_priority(-(2**31),1,False,2**31-1,99,True))

    def test_group_filter_combines_value_and_duration_without_mutation(self):
        rows=[dict(duplication_group='attack',value=-5,duration=0),
              dict(duplication_group='defense',value=-99,duration=9),
              dict(duplication_group='attack',value=-10,duration=2)]
        before=deepcopy(rows)
        self.assertEqual(addition_for_group(rows,'attack'),
            dict(duplication_group='attack',value=-15,duration=2))
        self.assertEqual(rows,before)

    def test_absent_cancelled_and_wrapped_zero_returns_no_correction(self):
        self.assertIsNone(addition_for_group([],'attack'))
        self.assertIsNone(addition_for_group([dict(duplication_group='other',value=1)],'attack'))
        self.assertIsNone(addition_for_group([dict(duplication_group='attack',value=v,duration=0)
            for v in (-5,5)],'attack'))
        self.assertIsNone(addition_for_group([dict(duplication_group='attack',value=-(2**31))]*2,'attack'))
        self.assertEqual(addition_for_group([dict(duplication_group='attack',duration=1)],'attack')['value'],0)

    def test_signed_wrap_and_exact_group_identity(self):
        rows=[dict(duplication_group='Attack',value=100),dict(duplication_group='attack',value=2**31-1),
              dict(duplication_group='attack',value=1,duration=-1)]
        self.assertEqual(addition_for_group(rows,'attack'),
                         dict(duplication_group='attack',value=-(2**31),duration=-1))

if __name__=='__main__':unittest.main()
