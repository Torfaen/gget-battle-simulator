import unittest
from copy import deepcopy
from battle_headless.terrain_capability import upgrade,from_sources


class TerrainUpgradeTests(unittest.TestCase):
    def base(self):return {kind:1 for kind in range(2,7)}
    def change(self,kind,source,target):return dict(target_terrain_type=kind,from_capability=source,to_capability=target)
    def test_null_and_empty_parts_keep_original_values(self):
        self.assertEqual(upgrade(self.base(),None)['capabilities'],self.base())
        self.assertEqual(upgrade(self.base(),[])['capabilities'],self.base())
    def test_part_changes_match_original_base_not_previous_change(self):
        result=upgrade(self.base(),[self.change(2,1,2),self.change(2,2,3)])
        self.assertEqual(result['capabilities'][2],2)
    def test_last_base_match_wins_unknown_target_ignored_and_inputs_pure(self):
        rows=[self.change(2,1,3),self.change(2,1,0),self.change(1,1,3)];base=self.base();before=deepcopy((base,rows))
        self.assertEqual(upgrade(base,rows)['sorted_standby_terrains'],[2]);self.assertEqual((base,rows),before)
    def test_sources_compute_upgrade_without_hand_entered_upgraded_values(self):
        source=dict(base_values=self.base(),corrections=[],option_parts_changes=[self.change(2,1,3)])
        self.assertEqual(from_sources(source)['capabilities'][2],3)
    def test_registered_trait_branch_uses_base_and_does_not_need_parts(self):
        source=dict(base_values=self.base(),corrections=[self.change(2,3,2)],option_parts_changes=[self.change(2,1,3)])
        self.assertEqual(from_sources(source)['capabilities'][2],1)
        del source['option_parts_changes'];self.assertEqual(from_sources(source)['capabilities'][2],1)
    def test_unknown_upgrade_source_rejected_and_captured_upgraded_state_preserved(self):
        source=dict(base_values=self.base(),corrections=[])
        with self.assertRaises(NotImplementedError):from_sources(source)
        source['upgraded_values']={kind:3 for kind in range(2,7)}
        self.assertEqual(from_sources(source)['sorted_standby_terrains'],[2,4,3,5,6])


if __name__=='__main__':unittest.main()
