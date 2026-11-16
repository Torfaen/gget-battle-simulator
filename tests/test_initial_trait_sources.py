import unittest
from copy import deepcopy
from types import SimpleNamespace
from battle_headless.trait_factory import RuntimeTraitStateFactory
from battle_headless.trait_sources import (account_initial_sources,acquired_character_abilities,
    capture_time_ms,materialize_initial_sources,resolved_initial_sources,schedule_contains,npc_initial_sources)


class InitialTraitSourcesTests(unittest.TestCase):
    def master(self):
        def trait(identity,kind,value,timing=0,limit=0):
            return dict(Id=identity,TraitTypeIndex=kind,TraitValue=value,ActionTimingTypeIndex=timing,
                Limit=limit,ActiveConditionSetId=0,ValueSourceConditionSetId=0,TraitBoostConditionSetId=0,
                TargetTraitDuplicationGroupId=0,TargetWeaponConditionId=0,TargetConditionSetId=1)
        rows={100:trait(100,7,5),101:trait(101,9,10),200:trait(200,13,10,11,2)}
        return SimpleNamespace(index={'m_trait':rows,
            'm_ability':{101:dict(TraitSetId=10),102:dict(TraitSetId=20),
                         201:dict(TraitSetId=40),202:dict(TraitSetId=30)},
            'm_option_parts':{7:dict(TraitSetId=30)},
            'm_schedule':{1:dict(StartDatetime=100,EndDatetime=200)},
            'm_trait_condition_target_weapon':{}},tables={
                'm_trait_set_detail':[dict(TraitSetId=value,IsEnabledWithGrantOwnerActive=False,IsEnabledWhileAllTraitsUsable=False,
                    IsStackable=False,DuplicationGroupId='') for value in (10,20,30,40)],
                'm_trait_set':[dict(Id=10,TraitId=100),dict(Id=10,TraitId=100),
                               dict(Id=30,TraitId=101),dict(Id=40,TraitId=200)],
                'm_trait_condition':[dict(Id=1,TraitConditionSetId=1,GroupId=0,TargetTypes='Owner')]})

    def configuration(self):
        return dict(unit_ability_configuration=[dict(AbilityId=101,SortOrder=2),dict(AbilityId=102,SortOrder=1)],
            option_parts_id=7,character_level=80,character_specialized=False,
            character_ability_configuration=[dict(AbilityId=201,SpAbilityId=202,RequiredLevel=80,
                                                   SortOrder=1,ScheduleId=1)])

    def test_order_source_types_empty_sets_and_duplicate_trait_instances(self):
        master=self.master();plan=account_initial_sources(master,'a',self.configuration(),now_ms=100)
        self.assertEqual([(r['source_type'],r['source_id']) for r in plan],[(1,102),(1,101),(3,7),(2,201)])
        factory=RuntimeTraitStateFactory()
        report=materialize_initial_sources(master,plan,dict(turn=0,id=0),factory)
        self.assertEqual(report['created_set_ids'],[1,2,3])
        self.assertEqual(len(report['skipped_empty_sources']),1)
        groups=report['groups']
        self.assertEqual([t['master_trait_id'] for t in groups[1]['values']],[100,100])
        self.assertEqual([t['runtime_trait_id'] for t in groups[1]['values']],[1,2])
        self.assertEqual(groups[2]['source_type'],3)
        self.assertEqual(groups[3]['source_type'],2)
        self.assertIsNone(groups[3]['source_unit_id'])
        self.assertEqual(report['pending_effect_set_ids'],[1,2])
        self.assertTrue(groups[3]['effect_registration_complete'])

    def test_normal_schedule_includes_end_and_sp_bypasses_schedule(self):
        master=self.master();config=self.configuration()
        for now,expected in ((99,[]),(100,[201]),(200,[201]),(201,[])):
            self.assertEqual([r['AbilityId'] for r in acquired_character_abilities(master,config,now)],expected)
        config['character_specialized']=True
        self.assertEqual([r['AbilityId'] for r in acquired_character_abilities(master,config,None)],[202])
        config['character_ability_configuration'][0]['SpAbilityId']=0
        self.assertEqual([r['AbilityId'] for r in acquired_character_abilities(master,config,None)],[201])
        config['character_level']=79
        self.assertEqual(acquired_character_abilities(master,config,None),[])

    def test_missing_time_is_explicit_and_config_primary_order_must_be_unique(self):
        master=self.master();config=self.configuration()
        with self.assertRaises(NotImplementedError):account_initial_sources(master,'a',config)
        config['unit_ability_configuration'][1]['SortOrder']=2
        with self.assertRaises(ValueError):account_initial_sources(master,'a',config,now_ms=100)

    def test_materialization_preflights_all_sources_without_advancing_factory(self):
        master=self.master();plan=account_initial_sources(master,'a',self.configuration(),now_ms=100)
        plan[-1]['master_trait_set_id']=999
        factory=RuntimeTraitStateFactory();groups={};before=deepcopy(vars(factory))
        with self.assertRaises(KeyError):materialize_initial_sources(master,plan,dict(turn=0,id=0),factory,groups)
        self.assertEqual(groups,{})
        self.assertEqual(vars(factory),before)

    def test_initial_character_source_enters_first_phase_damaged_chain(self):
        from battle_headless.trait_grants import execute_owner_grants
        master=self.master();factory=RuntimeTraitStateFactory()
        # Explicit already acquired span; creation phase is supplied by the stage caller.
        plan=resolved_initial_sources(master,'a',[],[],[dict(AbilityId=201)])
        groups=materialize_initial_sources(master,plan,dict(turn=0,id=0),factory)['groups']
        unit=dict(id='a',existence=0,attack=100,defense=200,scalar_corrections={},
                  _passive_initial_state=dict(attack=100,defense=200))
        execute_owner_grants(master,factory,groups,[unit],'a',dict(turn=1,id=0),timing=11)
        self.assertEqual(unit['attack'],110)  # Two distinct master links each add +5.
        self.assertEqual(groups[1]['values'][0]['used_count'],1)
        self.assertEqual(groups[2]['source_type'],5)

    def test_capture_clock_is_explicit_and_reproducible(self):
        self.assertEqual(capture_time_ms('1970-01-01T00:00:00+00:00'),0)
        self.assertEqual(capture_time_ms('1970-01-01T08:00:00.001999+08:00'),1)
        with self.assertRaises(ValueError):capture_time_ms('2026-10-19T12:00:00')
        self.assertIsNone(capture_time_ms(None))
        self.assertTrue(schedule_contains(dict(StartDatetime=100,EndDatetime=100),100))

    def test_npc_dedicated_sources_order_and_zero_set_do_not_fall_back(self):
        master=self.master()
        master.tables['m_map_npc_unit_ability_set_content']=[
            dict(MapNpcUnitAbilitySetId=9,SortOrder=2,AbilityId=101),
            dict(MapNpcUnitAbilitySetId=9,SortOrder=1,AbilityId=102)]
        master.tables['m_map_npc_character_ability_set_content']=[
            dict(MapNpcCharacterAbilitySetId=8,SortOrder=1,AbilityId=201)]
        plan=npc_initial_sources(master,'npc',dict(MapNpcUnitAbilitySetId=9),dict(MapNpcCharacterAbilitySetId=8))
        self.assertEqual([(r['source_type'],r['source_id']) for r in plan],[(1,102),(1,101),(2,201)])
        self.assertEqual(npc_initial_sources(master,'npc',dict(MapNpcUnitAbilitySetId=0),
                                             dict(MapNpcCharacterAbilitySetId=0)),[])


if __name__=='__main__':unittest.main()
