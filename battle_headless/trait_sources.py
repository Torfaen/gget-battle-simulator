"""Original initial trait-source ordering; activation stays in its own service."""
from .native_priority_sort import priority_sort
from .original import i32
from copy import deepcopy
from .trait_factory import i64
from .mechanics import short


def schedule_contains(schedule,now_ms):
    """EternalDateTime.IsWithinPeriod: inclusive start AND inclusive end."""
    return i64(schedule['StartDatetime'])<=i64(now_ms)<=i64(schedule['EndDatetime'])


def capture_time_ms(value):
    """Explicit timezone-bearing capture timestamp, without consulting wall time."""
    from datetime import datetime,timezone
    if value is None:return None
    instant=datetime.fromisoformat(value)
    if instant.tzinfo is None:raise ValueError('Capture timestamp must include its timezone')
    delta=instant.astimezone(timezone.utc)-datetime(1970,1,1,tzinfo=timezone.utc)
    return delta.days*86400000+delta.seconds*1000+delta.microseconds//1000


def _configuration_order(rows):
    rows=list(rows)
    # These master tables have a (unit/character, SortOrder) primary key.
    # Duplicate ability IDs at different orders remain distinct sources.
    keys=[i32(row['SortOrder']) for row in rows]
    if len(set(keys))!=len(keys):raise ValueError('Duplicate ability configuration SortOrder')
    return sort_abilities(rows)


def acquired_character_abilities(master,configuration,now_ms):
    """MCharacter selection then UserCharacterEntity acquired-level filtering.

    Normal characters apply nonzero schedules; SP bypasses that schedule branch
    and uses SpAbilityId when nonzero, falling back to AbilityId. Levels are
    signed Int16 in the original table/entity path.
    """
    result=[];specialized=bool(configuration.get('character_specialized',False))
    level=short(configuration.get('character_level',0))
    for row in _configuration_order(configuration.get('character_ability_configuration',())):
        schedule_id=row.get('ScheduleId',0)
        if not specialized and schedule_id:
            if now_ms is None:raise NotImplementedError('Explicit character ability schedule time required')
            if not schedule_contains(master.index['m_schedule'][schedule_id],now_ms):continue
        if short(row['RequiredLevel'])>level:continue
        identity=(row.get('SpAbilityId') or row['AbilityId']) if specialized else row['AbilityId']
        result.append(dict(row,AbilityId=identity))
    return result


def account_initial_sources(master,owner_id,configuration,*,now_ms=None):
    """Resolve the current snapshot's unit/part/character source sequence."""
    units=_configuration_order(configuration['unit_ability_configuration'])
    characters=acquired_character_abilities(master,configuration,now_ms)
    parts=[configuration['option_parts_id']] if configuration.get('option_parts_id') else []
    return resolved_initial_sources(master,owner_id,units,parts,characters)


def npc_initial_sources(master,owner_id,unit_row,character_row):
    """Dedicated NPC lists, without falling back to normal-unit abilities."""
    units=_configuration_order(row for row in master.tables['m_map_npc_unit_ability_set_content']
        if row['MapNpcUnitAbilitySetId']==unit_row['MapNpcUnitAbilitySetId'])
    characters=_configuration_order(row for row in master.tables['m_map_npc_character_ability_set_content']
        if row['MapNpcCharacterAbilitySetId']==character_row['MapNpcCharacterAbilitySetId'])
    return resolved_initial_sources(master,owner_id,units,(),characters)


def attach_initial_source_plans(env,stage):
    """Attach base source provenance; do not invent a global creation phase/order.

    Runtime materialization needs the actual stage creation phase and complete
    unit creation stream (including not-yet-deployed reinforcements). It is a
    separate explicit operation until those stage boundaries are connected.
    """
    npcs={npc['placement']['Id']:npc for npc in stage.npcs};plans={}
    for unit in env.units.values():
        if 'account_configuration' in unit:
            config=unit['account_configuration']
            plan=account_initial_sources(stage.master,unit['id'],config,
                now_ms=config.get('trait_source_time_ms'))
        else:
            npc=npcs[unit['source_npc_id']]
            plan=npc_initial_sources(stage.master,unit['id'],npc['units'][0],npc['characters'][0])
        plans[unit['id']]=plan
    for identity,plan in plans.items():env.units[identity]['initial_trait_sources']=plan
    env.initial_trait_source_plan_scope='base unit/parts/character; stage-added sources not included'
    return plans


def sort_abilities(rows):
    """Unit/character ability lists compare signed SortOrder, ascending.

    Use the client's reference-array introsort so equal keys retain the native
    permutation, rather than assuming a stable Python sort.
    """
    def compare(a,b):
        left,right=i32(a['SortOrder']),i32(b['SortOrder'])
        return (left>right)-(left<right)
    return priority_sort(rows,compare=compare)


def resolved_initial_sources(master,owner_id,unit_abilities,part_ids,character_abilities):
    """Initial converter order over already resolved native ability spans.

    Unit abilities -> option parts -> character abilities. Do not sort an
    already resolved span again: repeated native sorts can permute equal keys.
    Character level/schedule/SP selection must precede this function.
    """
    sources=[]
    for kind,source_type,rows,table in (
            ('unit_ability',1,unit_abilities,'m_ability'),
            ('option_parts',3,[dict(AbilityId=value) for value in part_ids],'m_option_parts'),
            ('character_ability',2,character_abilities,'m_ability')):
        for row in rows:
            identity=row['AbilityId'];definition=master.index[table][identity]
            sources.append(dict(owner_unit_id=owner_id,source_type=source_type,
                source_id=identity,source_name='',source_unit_id=owner_id,
                master_trait_set_id=definition['TraitSetId'],source_kind=kind))
    return sources


def materialize_initial_sources(master,sources,current_phase,factory,groups=None):
    """Create all explicit source states atomically; effect activation is separate.

    Empty sets do not allocate identities (CreateAndSave). Preserve duplicate
    definitions and source occurrences. Unsupported concrete effects remain
    unregistered, so callers cannot mistake this inventory for battle readiness.
    """
    if groups is None:groups={}
    trial=deepcopy(factory);created=[];skipped=[]
    details={row['TraitSetId']:row for row in master.tables['m_trait_set_detail']}
    linked={row['Id'] for row in master.tables['m_trait_set']}
    for source in sources:
        identity=source['master_trait_set_id']
        details[identity]  # Validate even an empty master source before commit.
        if identity not in linked:
            skipped.append(deepcopy(source));continue
        group=trial.create_set(master,identity,source['owner_unit_id'],current_phase,
            **{key:source[key] for key in ('source_type','source_id','source_name','source_unit_id')})
        group['initial_source_kind']=source['source_kind']
        created.append(group)
    group_ids=set(groups);trait_ids={t['runtime_trait_id'] for group in groups.values() for t in group['values']}
    for group in created:
        if group['runtime_set_id'] in group_ids:raise ValueError('Initial source factory identity collision')
        group_ids.add(group['runtime_set_id'])
        for trait in group['values']:
            if trait['runtime_trait_id'] in trait_ids:raise ValueError('Initial source trait identity collision')
            trait_ids.add(trait['runtime_trait_id'])
    from .trait_grants import register_on_demand_sources
    ready={group['runtime_set_id']:group for group in created}
    register_on_demand_sources(ready)
    groups.update(ready)
    factory.trait_counter=trial.trait_counter;factory.set_counter=trial.set_counter
    return dict(groups=groups,created_set_ids=list(ready),skipped_empty_sources=skipped,
        pending_effect_set_ids=[group['runtime_set_id'] for group in created
                                if not group['effect_registration_complete']])
