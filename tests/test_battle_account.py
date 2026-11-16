import unittest
from copy import deepcopy
from battle_headless.account import DEFAULT_SNAPSHOT, guarded, import_formation, build_account_env
from battle_headless.__main__ import choose


class AccountTests(unittest.TestCase):
    def test_captured_guard_values_and_verifier(self):
        # Independent live captures: Sazabi base HP=9960 and growth=10000.
        hp={'bytes':'01000000e66b5cfa0100000040d2a8910000000004c55d40012a9024'}
        growth={'bytes':'010000001d8a4bf501000000a02a61da00000000a0878f7987301f9b'}
        self.assertEqual(guarded(hp),9960)
        self.assertEqual(guarded(growth),10000)
        support_limit={'bytes':'0100000078ebddea01000000df1a1b5800001150c9430000'}
        self.assertEqual(guarded(support_limit,16),3)
        broken=deepcopy(hp)
        broken['bytes']=broken['bytes'][:-2]+'00'
        with self.assertRaisesRegex(ValueError,'verifier mismatch'):guarded(broken)

    @unittest.skipUnless(DEFAULT_SNAPSHOT.exists(),'Requires the private local account capture')
    def test_current_formation_import_and_headless_execution(self):
        formation=import_formation()
        self.assertEqual(formation['source_content_target_id'],90510001)
        self.assertEqual([len(f['slots']) for f in formation['forces']],[5,5])
        first=formation['forces'][0]['slots'][0]
        self.assertEqual(first['unit_id'],1110000550)
        self.assertEqual((first['level'],first['limit_break']),(100,3))
        self.assertEqual(first['stats']['hp'],100396)  # 71712 + truncate(40%).
        self.assertEqual(first['pilot']['Ranged'],595)
        self.assertEqual(len({s['user_unit_id'] for f in formation['forces'] for s in f['slots']}),10)
        a,stage=build_account_env(formation);b,_=build_account_env(formation,stage)
        self.assertEqual(stage.stage_id,90510005)
        self.assertEqual(a.active_team,'player')
        self.assertNotIn('test_player',a.units)
        self.assertEqual(sum(u['team']=='player' for u in a.units.values()),10)
        self.assertEqual(len({tuple(u['position']) for u in a.units.values()}),len(a.units))
        while not (a.terminated or a.truncated):
            action=choose(a)
            self.assertEqual(a.step(action),b.step(action))
        self.assertGreater(sum(e['action']['type']=='attack' for e in a.events),0)

    @unittest.skipUnless(DEFAULT_SNAPSHOT.exists(),'Requires the private local account capture')
    def test_open_scheduled_character_passive_is_imported_and_changes_stats(self):
        formation=import_formation();active,stage=build_account_env(formation)
        first=formation['forces'][0]['slots'][0]
        start=stage.master.index['m_schedule'][2510309902]['StartDatetime']
        inactive,_=build_account_env(formation,stage,trait_source_time_ms=start-1)
        a,b=active.units[first['id']],inactive.units[first['id']]
        self.assertIn((2,2000301),[(source['source_type'],source['source_id'])
                                   for source in a['initial_trait_sources']])
        self.assertEqual(a['scalar_corrections'].get('CharacterAwakenRate',0)-
                         b['scalar_corrections'].get('CharacterAwakenRate',0),5)
        self.assertGreater(a['pilot']['Awaken'],b['pilot']['Awaken'])
        self.assertEqual(a['account_configuration']['trait_source_time_basis'],'snapshot_capture')


if __name__=='__main__':unittest.main()
