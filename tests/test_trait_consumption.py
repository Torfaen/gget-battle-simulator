import unittest
from battle_headless.trait_consumption import get_limit,remaining_value,is_active,is_removable,consume,consume_by_trait_id


class TraitConsumptionTests(unittest.TestCase):
    def trait(self,**changes):
        return dict(dict(runtime_trait_id=2**40,master_trait_id=7,action_timing=6,
            base_limit=2,additional_limit=0,used_count=0,trait_type=49),**changes)
    def test_unlimited_base_ignores_additional_limit_but_count_still_changes(self):
        trait=self.trait(base_limit=0,additional_limit=100)
        self.assertEqual(get_limit(trait),0);self.assertTrue(consume(trait,6))
        self.assertEqual(remaining_value(trait),-1);self.assertTrue(is_active(trait))
        self.assertFalse(is_removable(trait))
    def test_consumption_matches_timing_without_its_own_eligibility_gate(self):
        trait=self.trait(used_count=3)
        self.assertFalse(is_active(trait));self.assertTrue(is_removable(trait))
        self.assertFalse(consume(trait,7));self.assertEqual(trait['used_count'],3)
        self.assertTrue(consume(trait,6));self.assertEqual(trait['used_count'],4)
    def test_overflow_and_special_type_removal_rule(self):
        trait=self.trait(base_limit=2147483647,additional_limit=1,used_count=2147483647,trait_type=114)
        self.assertEqual(get_limit(trait),-2147483648);self.assertFalse(is_active(trait))
        self.assertFalse(is_removable(trait));consume(trait,6)
        self.assertEqual(trait['used_count'],-2147483648)
    def test_runtime_identity_first_match_and_master_identity_is_not_used(self):
        first=self.trait(action_timing=7);second=self.trait()
        group=dict(values=[first,second])
        self.assertFalse(consume_by_trait_id(group,7,{},6))
        self.assertFalse(consume_by_trait_id(group,2**40,{},6))
        self.assertEqual([t['used_count'] for t in group['values']],[0,0])
    def test_phase_is_stamped_only_on_successful_turn_progress_consumption(self):
        trait=self.trait(action_timing=3);group=dict(values=[trait]);phase=dict(turn=2,id=0)
        self.assertFalse(consume_by_trait_id(group,trait['runtime_trait_id'],phase,6))
        self.assertNotIn('turn_progress_consumed_phase_value',group)
        self.assertTrue(consume_by_trait_id(group,trait['runtime_trait_id'],phase,3))
        self.assertIs(group['turn_progress_consumed_phase_value'],phase)


if __name__=='__main__':unittest.main()
