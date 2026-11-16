"""Build the fixed account/stage's ordered initial trait construction inventory.

The inventory is not the active effect repository. Always/StageStart/FirstTurn
activation and stage-script additions must be connected before it can replace
the explicitly limited passive profile used by the experimental runner.
"""
from copy import deepcopy
from dataclasses import asdict
from .initial_identities import InitialIdentitySequencer,npc_creation_order
from .trait_factory import RuntimeTraitStateFactory,creation_phase,i64
from .trait_sources import account_initial_sources,npc_initial_sources
from .supporters import leader_condition
from .original import i32,SOURCE_LIB_SHA256


LEADER_TRAIT_TYPES={1:7,2:8,3:9,4:10,5:31,6:32,7:33,8:34,9:11,10:12}


def _supporter_set(factory,owner,source_type,source_id,source_name,definitions):
    """Supporter overloads always use BeforeStart and have no master trait IDs.

    Definitions are typed supporter descriptions, never invented m_trait rows.
    Configuration row IDs remain provenance only.
    """
    set_id=factory.next_set_id();created=creation_phase(None)
    group=dict(runtime_set_id=set_id,master_trait_set_id=0,owner_unit_id=owner,
        source_type=source_type,source_id=i64(source_id),source_name=source_name,source_unit_id=None,
        created_phase_value=created,turn_progress_consumed_phase_value=created,
        enabled_internal=True,enabled_while_all_traits_usable=False,
        definition_kind='supporter',stack_count=1,values=[],prediction=False,
        effect_registration_complete=not definitions)
    for definition in definitions:
        group['values'].append(dict(runtime_trait_id=factory.next_trait_id(),parent_set_id=set_id,
            master_trait_id=0,owner_unit_id=owner,source_type=source_type,source_id=i64(source_id),
            source_unit_id=None,trait_type=definition['trait_type'],trait_value=i32(definition['value']),
            action_timing=0,base_limit=0,additional_limit=0,additional_trait_value=0,
            stack_count=1,used_count=0,supporter_definition=deepcopy(definition)))
    return group


def build_initial_trait_inventory(env,stage,formation):
    """Original own group -> guest group -> ascending NPC-ID creation sequence.

    Narrow, explicit preflight for the captured full two-group formation and
    twelve single-form NPCs. Unknown additions cannot silently alter identities.
    All work is local until the caller attaches the completed inventory.
    """
    if stage.stage_id!=90510005:raise NotImplementedError('Initial inventory is scoped to stage 90510005')
    if [force['number'] for force in formation['forces']]!=[1,2]:
        raise NotImplementedError('Original own/guest formation selection required')
    if any([s['slot'] for s in f['slots']]!=[1,2,3,4,5] or len(f['supporters'])!=1 for f in formation['forces']):
        raise NotImplementedError('Initial inventory requires five units and one main supporter per group')
    unsupported=set(stage.source)-{'m_map_group','m_map_npc','m_map_stage_condition',
        'm_map_stage_group_initial_placement','m_map_stage_own_ai'}
    if unsupported:raise NotImplementedError(f'Additional stage initial sources: {sorted(unsupported)}')
    if len(stage.npcs)!=12 or any(len(n['units'])!=1 or len(n['characters'])!=1 or
            not n['placement']['IsInitiallyPlaced'] or n['placement']['IsInitiallyOnWarship'] or
            n['placement']['MapNpcBuffId'] for n in stage.npcs):
        raise NotImplementedError('NPC transformations, hidden/reinforcement units and buffs require explicit creation sources')
    expected={s['id'] for f in formation['forces'] for s in f['slots']}
    npc_units={u['source_npc_id']:u for u in env.units.values() if 'source_npc_id' in u}
    expected.update(u['id'] for u in npc_units.values())
    if expected!=set(env.units) or set(npc_units)!={n['placement']['Id'] for n in stage.npcs}:
        raise ValueError('Initial inventory unit membership differs from the fixed creation stream')
    master=stage.master;factory=RuntimeTraitStateFactory();identities=InitialIdentitySequencer()
    groups={};events=[];unit_ids={};owners={};skipped=[]
    linked={row['Id'] for row in master.tables['m_trait_set']}
    details={row['TraitSetId'] for row in master.tables['m_trait_set_detail']}

    def save(group):
        groups[group['runtime_set_id']]=group
        events.append(dict(kind='trait_set',runtime_set_id=group['runtime_set_id'],
            owner_unit_id=group['owner_unit_id'],source_type=group['source_type'],source_id=group['source_id'],
            runtime_trait_ids=[t['runtime_trait_id'] for t in group['values']]))

    def unit_sources(unit,plan,*,player):
        native_id=identities.next_unit_id()
        unit_ids[unit['id']]=dict(native_unit_id=native_id)
        events.append(dict(kind='unit_id',unit=unit['id'],native_unit_id=native_id))
        for source in plan:
            master_id=source['master_trait_set_id']
            if master_id not in details:raise KeyError(master_id)
            if master_id not in linked:
                skipped.append(deepcopy(source));continue
            group=factory.create_set(master,master_id,unit['id'],None,
                **{k:source[k] for k in ('source_type','source_id','source_name','source_unit_id')})
            group['initial_source_kind']=source['source_kind'];save(group)
        if player:
            unit_ids[unit['id']]['native_unit_step_order']=identities.next_player_step_order()
        else:
            unit_ids[unit['id']]['source_npc_id']=unit['source_npc_id']

    for force in formation['forces']:
        recipients=[env.units[slot['id']] for slot in force['slots']]
        for unit in recipients:
            configuration=unit['account_configuration']
            unit_sources(unit,account_initial_sources(master,unit['id'],configuration,
                now_ms=configuration.get('trait_source_time_ms')),player=True)
        supporter=force['supporters'][0];definition=supporter['original_definition']
        contents=definition['leader_contents']
        if len(contents)!=1:raise NotImplementedError('Multi-content leader recipient selection needs its original aggregate condition path')
        owner=identities.next_non_unit_owner_id()
        owners[owner]=dict(supporter_id=supporter['id'],user_supporter_id=i64(supporter['user_supporter_id']),
            formation_group=dict(link_type=2,side=0,id='account:'+str(force['number'])))
        events.append(dict(kind='non_unit_owner',owner_unit_id=owner,**owners[owner]))
        growth=[dict(kind='growth',trait_type=kind,value=definition[field],
            active_conditions=[],target='SameGroup',is_using_max=False)
            for kind,field in ((32,'additional_hp'),(8,'additional_attack'))]
        save(_supporter_set(factory,owner,7,supporter['user_supporter_id'],supporter['name'],growth))
        content=contents[0]
        for unit in recipients:
            match=leader_condition(master,unit,content['conditions'])
            if match is None:raise NotImplementedError('Unsupported leader recipient condition in initial creation')
            if not match:continue
            traits=[]
            for trait in content['traits']:
                if trait['TraitTypeIndex'] not in LEADER_TRAIT_TYPES:
                    raise NotImplementedError('Unsupported leader trait type in initial creation')
                traits.append(dict(kind='leader',configuration_trait_id=trait['Id'],
                    trait_type=LEADER_TRAIT_TYPES[trait['TraitTypeIndex']],value=trait['Value'],
                    active_conditions=[],target_conditions=deepcopy(content['conditions']),
                    is_using_max=definition['leader']['IsUsingMaxTraitValue']))
            save(_supporter_set(factory,unit['id'],8,definition['leader']['Id'],'',traits))
    for npc in npc_creation_order(stage.npcs):
        unit=npc_units[npc['placement']['Id']]
        unit_sources(unit,npc_initial_sources(master,unit['id'],npc['units'][0],npc['characters'][0]),player=False)
    return dict(schema='initial-trait-construction-v1',libil2cpp_sha256=SOURCE_LIB_SHA256,
        master_sha256=master.sha256,creation_phase=asdict(creation_phase(None)),
        unit_identities=unit_ids,non_unit_owners=owners,creation_events=events,groups=groups,
        skipped_empty_sources=skipped,counters=dict(unit=identities.unit_counter,
            player_step=identities.step_counter,non_unit_owner=identities.non_unit_counter,
            trait_set=factory.set_counter,trait=factory.trait_counter),
        active_repository_connected=False,complete_global_initialization=False,
        pending=['Always/StageStart/FirstTurnStart global activation and effect registration',
                 'Stage-script/reinforcement sources require original stage evidence',
                 'NPC UnitStepOrder group-formation source mapping and complete deployment',
                 'Localized base source names and presentation-only constructor fields'])


def inventory_report(inventory):
    """JSON-safe construction states, kept separate from live effect state."""
    result=deepcopy(inventory)
    for group in result['groups'].values():
        for field in ('created_phase_value','turn_progress_consumed_phase_value'):
            group[field]=asdict(group[field])
    return result


def attach_initial_trait_inventory(env,stage,formation):
    inventory=build_initial_trait_inventory(env,stage,formation)
    for identity,values in inventory['unit_identities'].items():env.units[identity].update(values)
    env.initial_trait_inventory=inventory
    return inventory
