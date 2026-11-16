import unittest
from copy import deepcopy
from battle_headless.combat_primitives import (character_attack_value,initial_mp,valid_tension,
    resource_percent,normal_counter_policy,ScalarCorrections)
from battle_headless.conditions import hp_threshold,en_threshold,turn_threshold,attack_distance
from battle_headless.account import DEFAULT_SNAPSHOT,import_formation,build_account_env
from battle_headless.owner_passives import attach_owner_passives


class ExtensionTests(unittest.TestCase):
    def test_supporter_growth_and_lossless_leader_conditions(self):
        from battle_headless.supporters import additional_parameter
        self.assertEqual(additional_parameter(1700,11200),1904)
        self.assertEqual(additional_parameter(180,11200),202)
        if not DEFAULT_SNAPSHOT.exists():self.skipTest('Local account capture unavailable')
        formation=import_formation()
        definitions=[s['original_definition'] for f in formation['forces'] for s in f['supporters']]
        self.assertEqual([(d['additional_hp'],d['additional_attack']) for d in definitions],[(1904,202),(831,84)])
        self.assertEqual([d['leader_contents'][0]['conditions'][0]['TargetTypes'] for d in definitions],['SameGroup','SameGroup'])
        self.assertEqual(len(definitions[0]['leader_contents'][0]['traits']),4)
        self.assertTrue(all(d['runtime_profile']=='growth-and-scalar-leader-v1' for d in definitions))

    def test_multi_attribute_max_and_initial_mp(self):
        pilot=dict(Ranged=595,Melee=582,Awaken=730)
        self.assertEqual(character_attack_value(pilot,[1,3]),730)
        self.assertEqual(character_attack_value(pilot,[]),0)
        self.assertEqual([initial_mp(i) for i in [-1,0,6,12,100]],[0,0,6,12,12])
        self.assertFalse(valid_tension(2,4));self.assertTrue(valid_tension(5,4))

    def test_max_group_replaces_same_key_and_does_not_sum_max_fields(self):
        c=ScalarCorrections();c.add({'AttackRate':5,'CharacterRangedRate':1})
        c.replace_group('a',{'AttackRate':20,'CharacterRangedRate':10})
        c.replace_group('b',{'AttackRate':30,'CharacterRangedRate':15})
        c.replace_group('a',{'AttackRate':-5,'CharacterRangedRate':2})
        self.assertEqual(c.total(),{'AttackRate':35,'CharacterRangedRate':18})
        with self.assertRaises(NotImplementedError):c.add({'DamageTakenCorrections':1})

    def test_hp_en_nullable_threshold_edges(self):
        self.assertTrue(hp_threshold(50,100,lte=50,gte=50))
        self.assertFalse(hp_threshold(51,100,lte=50))
        self.assertTrue(hp_threshold(0,0))
        self.assertFalse(hp_threshold(0,0,lte=0))
        self.assertFalse(hp_threshold(100,100,damaged=True))
        self.assertFalse(en_threshold(0,0,value_lte=1))
        self.assertTrue(turn_threshold(3,2));self.assertFalse(turn_threshold(1,2))
        self.assertTrue(attack_distance(None));self.assertFalse(attack_distance(None,gte=1))
        self.assertEqual(resource_percent(101,5),6)

    def test_counter_support_defense_changes_survival_branch(self):
        context=dict(hp=100,barrier=0,total_damage=300,hp_damage=300,
                     defended_total_damage=250,defended_hp_damage=250,manual_side=True)
        self.assertEqual(normal_counter_policy(**context),'evade_service')
        self.assertEqual(normal_counter_policy(**context,support_defense_enabled=True),'counter_service')
        self.assertEqual(normal_counter_policy(**context,preemptive_available=True),'preemptive_service')

    @unittest.skipUnless(DEFAULT_SNAPSHOT.exists(),'Requires the private local formation capture')
    def test_original_mp_ex_weapons_and_dynamic_owner_hp_condition(self):
        formation=import_formation();env,stage=build_account_env(formation)
        first=formation['forces'][0]['slots'][0]
        self.assertEqual(len(first['weapons']),4)
        self.assertTrue(any(len(w['attack_attributes'])>1 for w in first['weapons']))
        self.assertEqual(env.units['account_1_1']['mp'],0)
        summary=attach_owner_passives(env,stage)
        self.assertGreater(sum(len(r['applied']) for r in summary),40)
        self.assertEqual(env.units['account_1_3']['mp'],6)
        unit=env.units['account_1_1'];before=unit['attack']
        unit['hp']=unit['max_hp']//2;unit['en']-=1
        env.legal_actions()
        self.assertGreater(unit['attack'],before)
        self.assertEqual(unit['hp'],unit['max_hp']//2)
        value=unit['attack'];hp=unit['hp'];en=unit['en']
        env.legal_actions();self.assertEqual((unit['attack'],unit['hp'],unit['en']),(value,hp,en))
        snapshot=env.observe()
        with self.assertRaises(ValueError):env.step({'type':'attack','unit':'missing'})
        self.assertEqual(snapshot,env.observe())

    @unittest.skipUnless(DEFAULT_SNAPSHOT.exists(),'Requires the private local formation capture')
    def test_owner_recovery_lifecycle_without_reinterpreting_stage_same_group(self):
        env,stage=build_account_env(import_formation())
        # This stage's real recovery targets SameGroup.
        # Use a controlled Owner fixture to verify timing, rounding and no revival.
        npc=next(u for u in env.units.values() if u['team']=='enemy')
        from battle_headless.mechanics import npc_passive_definitions
        rows=[]
        for source in stage.npcs:
            rows.extend(npc_passive_definitions(stage.master,source['units'][0],source['characters'][0]))
        original=next(t for t in rows if t['TraitTypeIndex']==3)
        self.assertEqual(original['TargetConditionSetId'],1000012)
        fixture=deepcopy(original);fixture['TargetConditionSetId']=1000001
        npc['_owner_traits']=[fixture]
        attach_owner_passives(env,stage)
        unit=next(u for u in env.units.values() if u['timed_passive_ids'])
        unit['hp']=unit['max_hp']//2
        before=unit['hp']
        result=env.step({'type':'end_turn'})
        self.assertEqual(unit['hp'],before)  # Other-side start does not invoke this use case.
        result=env.step({'type':'end_turn'})
        effects=result['event']['phase_start_effects']
        self.assertTrue(any(e['unit']==unit['id'] and e.get('amount',0)>0 for e in effects))
        self.assertGreater(unit['hp'],before)
        unit['hp']=0
        env.step({'type':'end_turn'});env.step({'type':'end_turn'})
        self.assertEqual(unit['hp'],0)

    @unittest.skipUnless(DEFAULT_SNAPSHOT.exists(),'Requires the private local formation capture')
    def test_supporter_group_selection_and_recipient_owned_leader_traits(self):
        env,stage=build_account_env(import_formation())
        first=env.units['account_1_1'];second=env.units['account_2_1']
        self.assertEqual({e['supporter'] for e in first['supporter_effects']},{1850000160})
        self.assertEqual({e['supporter'] for e in second['supporter_effects']},{1001000450})
        self.assertEqual(len(first['supporter_effects']),6)
        # Z and ZZ share this group with eligible recipients, but fail its series filter.
        for key in ['account_2_3','account_2_4']:
            unit=env.units[key]
            self.assertEqual([e['kind'] for e in unit['supporter_effects']],['growth','growth'])
            self.assertEqual(len(unit['inactive_supporter_effects']),1)
        self.assertTrue(all(not u['supporter_effects'] for u in env.units.values() if u['team']=='enemy'))
        self.assertEqual(first['max_hp'],116355)
        first['hp']-=123;first['en']-=1
        before=deepcopy(first)
        env.legal_actions();env.legal_actions()
        self.assertEqual(first,before)

    @unittest.skipUnless(DEFAULT_SNAPSHOT.exists(),'Requires the private local formation capture')
    def test_leader_maximum_groups_keep_distinct_runtime_traits(self):
        env,stage=build_account_env(import_formation())
        unit=env.units['account_1_1'];unit['_owner_traits']=[]
        template=deepcopy(env.supporter_sources[0]);sources=[]
        for index,value in enumerate([20,30]):
            source=deepcopy(template);source['id']+=index
            definition=source['definition'];definition['additional_hp']=definition['additional_attack']=0
            definition['leader']['IsUsingMaxTraitValue']=True
            content=definition['leader_contents'][0]
            # Deliberately reuse a master trait ID: original runtime trait IDs differ.
            content['traits']=[dict(Id=123,TraitTypeIndex=1,Value=value)]
            sources.append(source)
        env.supporter_sources=sources
        attach_owner_passives(env,stage)
        self.assertEqual(unit['scalar_corrections']['AttackRate'],30)
        self.assertEqual(unit['attack'],int(unit['_passive_initial_state']['attack']*1.3))


if __name__=='__main__':unittest.main()
