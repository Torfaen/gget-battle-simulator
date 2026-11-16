"""Original type-13 grant callback arguments and master stack adjustment.

The event service must select the active trait/recipient before calling here.
The callback owns duplicate-set arbitration, creation and registration; it must
not flatten child definitions or inherit the granting trait's timing/limit.
Evidence: trait_grants_native.json, _GrantTraitSet 0x5f946f8,
MasterTraitSet.AdjustStackCount 0x612a7d4.
"""
from .original import i32
from .trait_consumption import remaining_value
from copy import deepcopy


def adjust_stack_count(detail, count):
    """Normalize the signed stack count using the *receiving* master set."""
    if not detail['IsStackable']:
        return 1
    limit = i32(detail['StackLimit'])
    if limit <= 0:
        # Native routes this invalid master configuration through its assertion.
        raise ValueError('Stackable trait set requires a positive StackLimit')
    return max(1, min(limit, i32(count)))


def resolved_grant_request(trait, target_unit_id):
    """Arguments supplied by _GrantTraitSet to _GrantTraitSetInternal.

    No activation decision or consumption happens in this callback. The source
    is the granting trait's OwnerId, not its potentially different SourceUnitId.
    Child timings/limits remain the child set's definitions.
    """
    if trait['trait_type'] != 13:
        raise ValueError('TraitSetGrant requires trait type 13')
    return dict(master_trait_set_id=i32(trait['trait_value']),
                active_count=i32(trait['used_count'] + 1),
                target_unit_id=target_unit_id,
                source_unit_id=trait['owner_unit_id'],
                additional_trait_value=0, additional_limit=0)


def execute_resolved_grant(trait, target_unit_id, grant_set):
    """Invoke the required grant service once; source consumption is separate.

    This is the execution callback, not the simulation entry point. The native
    OnSimulate only constructs a result and never invokes OnGrant.
    """
    return grant_set(**resolved_grant_request(trait, target_unit_id))


def higher_priority(value, limit, unlimited, other_value, other_limit, other_unlimited):
    """Native _IsHigherPriority: magnitude, then unlimited/remaining limit.

    The magnitude comparison is unsigned after signed Int32 negation, including
    INT_MIN. Equal magnitude never displaces an unlimited incumbent.
    """
    magnitude = abs(i32(value))
    other_magnitude = abs(i32(other_value))
    if magnitude != other_magnitude:
        return magnitude > other_magnitude
    if other_unlimited:
        return False
    return bool(unlimited or i32(limit) > i32(other_limit))


def can_overwrite_set(existing, definitions, stack_count, *,
                      additional_trait_value=0, additional_limit=0):
    """Original set arbitration delegates to the first trait on each side.

    The incumbent's comparison limit is *remaining*, not its initial limit.
    Caller must first normalize stack_count with the receiving master set.
    Existing trait_value is the resolved runtime GetTraitValue value.
    """
    if not existing['values']:
        return True
    if not definitions:
        return False
    old = existing['values'][0]
    new = definitions[0]
    return higher_priority(i32(new['TraitValue'] * i32(stack_count) + additional_trait_value),
                           i32(new['Limit'] + additional_limit), new['Limit'] == 0,
                           old['trait_value'], remaining_value(old), old['base_limit'] == 0)


def grant_resolved_set(master, factory, groups, units, current_phase, *,
                       master_trait_set_id, active_count, target_unit_id,
                       source_unit_id, source_name, additional_trait_value=0,
                       additional_limit=0):
    """Create/register a resolved grant, replacing the first duplicate set.

    Supports the concrete owner builders registered by trait_effects. Activation,
    recipient selection and event consumption belong to the calling service.
    Unsupported child builders are preflighted before removing the incumbent.
    """
    from .trait_effects import (register_resolved_owner_combat_set, _can_remove,
                               _on_remove, _remove_stat, _remove_chance_step)
    from .trait_consumption import remove_set_all
    units = list(units)
    target = next(unit for unit in units if unit['id'] == target_unit_id)
    detail = next(row for row in master.tables['m_trait_set_detail']
                  if row['TraitSetId'] == master_trait_set_id)
    if detail.get('IsEnabledWithGrantOwnerActive'):
        raise NotImplementedError('Grant owner active-state propagation required')
    definitions = [master.index['m_trait'][row['TraitId']]
                   for row in master.tables['m_trait_set'] if row['Id'] == master_trait_set_id]
    stack = adjust_stack_count(detail, active_count)
    duplication = detail.get('DuplicationGroupId')
    old = next((group for group in groups.values()
                if duplication and group['owner_unit_id'] == target_unit_id
                and group['master_detail'].get('DuplicationGroupId') == duplication), None)
    if old is not None and not can_overwrite_set(old, definitions, stack,
            additional_trait_value=additional_trait_value, additional_limit=additional_limit):
        return dict(created=False, replaced_set_id=None, group=old)
    if old is not None:
        # Only known concrete removal callbacks can be safely preflighted.
        for trait in old['values']:
            for unit in units:
                effect = unit.get('runtime_trait_effects', {}).get(trait['runtime_trait_id'])
                if effect is not None and (effect.get('can_remove') is not _can_remove or
                        effect.get('remove') not in (_on_remove, _remove_stat, _remove_chance_step)):
                    raise NotImplementedError('Duplicate set concrete removal not verified')
    if not definitions:
        # CreateAndSave 0x5f84f40 skips factory/repository creation for an empty
        # master set. _GrantTraitSetInternal may already remove an empty incumbent.
        if old is not None:
            remove_set_all(old,units)
            del groups[old['runtime_set_id']]
        return dict(created=False,replaced_set_id=old['runtime_set_id'] if old else None,group=None)
    trial_factory = deepcopy(factory)
    group = trial_factory.create_set(master, master_trait_set_id, target_unit_id, current_phase,
        source_type=5, source_id=master_trait_set_id, source_name=source_name,
        source_unit_id=source_unit_id, additional_limit=additional_limit,
        stack_count=stack, additional_trait_value=additional_trait_value)
    existing_ids={t['runtime_trait_id'] for existing in groups.values() for t in existing['values']}
    if group['runtime_set_id'] in groups or any(t['runtime_trait_id'] in existing_ids for t in group['values']):
        raise ValueError('Grant factory must share the initial runtime identity sequence')
    trial_units = deepcopy(units)
    if old is not None:
        remove_set_all(old, trial_units)
    trial_target = next(unit for unit in trial_units if unit['id'] == target_unit_id)
    register_resolved_owner_combat_set(master, deepcopy(group), trial_target)
    # Commit only after all supported effects and replacement removal validate.
    if old is not None:
        remove_set_all(old, units)
        del groups[old['runtime_set_id']]
    register_resolved_owner_combat_set(master, group, target)
    factory.trait_counter = trial_factory.trait_counter
    factory.set_counter = trial_factory.set_counter
    groups[group['runtime_set_id']] = group
    return dict(created=True, replaced_set_id=old['runtime_set_id'] if old else None, group=group)


def execute_owner_grants(master, factory, groups, units, owner_id, current_phase, *,
                         turn_progress_policy=None, timing=10):
    """Resolved owner grants with the original timing-specific consumption.

    Only unconditional owner-targeted pure grant groups are supported here.
    Preflight the complete event on isolated state so unsupported later grants
    cannot leave an earlier child registered. Mixed concrete on-demand groups
    remain unsupported rather than being partially executed.
    """
    from .trait_turn_progress import standard_satisfies
    from .trait_consumption import is_active, consume_trait_count
    from .weapon_corrections import _conditions
    if timing not in (10,11,12,13):
        raise NotImplementedError('Grant event wrapper timing not verified')
    policy = turn_progress_policy or standard_satisfies
    units = list(units)
    policy_results = {}

    def eligible(group):
        identity=group['runtime_set_id']
        if identity not in policy_results:policy_results[identity]=policy(group,current_phase)
        return policy_results[identity]

    def execute(local_factory, local_groups, local_units):
        owner = next(unit for unit in local_units if unit['id'] == owner_id)
        selected = []
        for group in list(local_groups.values()):
            if group['owner_unit_id'] != owner_id:
                continue
            values = group['values']
            matches = [trait for trait in values if trait['action_timing'] == timing]
            if not matches:
                continue
            if len(matches) != len(values) or any(t['trait_type'] != 13 for t in matches):
                raise NotImplementedError('Mixed on-demand grant execution required')
            detail = group['master_detail']
            if group.get('prediction') or any(detail.get(key) for key in
                    ('DuplicationGroupId','IsStackable','IsEnabledWithGrantOwnerActive')):
                raise NotImplementedError('Source grant activation arbitration required')
            for trait in matches:
                row = trait['master_trait']
                if any(row[key] for key in ('ActiveConditionSetId','ValueSourceConditionSetId',
                        'TraitBoostConditionSetId','TargetTraitDuplicationGroupId','TargetWeaponConditionId')):
                    raise NotImplementedError('Conditional grant activation required')
                targets = _conditions(master, row['TargetConditionSetId'])
                if len(targets) != 1 or targets[0]['TargetTypes'] != 'Owner' or any(
                        value for key,value in targets[0].items()
                        if key not in {'Id','TraitConditionSetId','GroupId','TargetTypes'}):
                    raise NotImplementedError('Grant recipient selection required')
            if group['enabled_internal'] and eligible(group):
                selected.extend(t for t in matches if is_active(t))
        if selected and owner.get('existence',owner.get('chance_step',{}).get('existence')) not in (0,1):
            raise NotImplementedError('Live grant owner required')
        register_on_demand_sources(local_groups)
        results = []
        for trait in selected:
            request = resolved_grant_request(trait, owner_id)
            results.append(grant_resolved_set(master,local_factory,local_groups,local_units,current_phase,
                source_name='',**request))  # Presentation names are outside this numeric service.
        consumption = consume_trait_count(local_groups,local_units,current_phase,timing,
            **grant_event_consumption_scope(owner_id,
                {trait['runtime_trait_id'] for trait in selected},timing))
        always=None
        if timing in (11,12):
            from .trait_effects import repeat_resolved_unconditional_always
            always=repeat_resolved_unconditional_always(local_groups,local_units,owner_id)
        return dict(results=results,consumption=consumption,always=always)

    execute(deepcopy(factory),deepcopy(groups),deepcopy(units))
    return execute(factory,groups,units)


def grant_event_consumption_scope(owner_id,executed_ids,timing):
    """Native wrapper filters; an empty ID set must stay empty, not become None."""
    if timing not in (10,11,12,13):raise NotImplementedError('Grant event timing not verified')
    return dict(owner_ids=None if timing==11 else {owner_id},
                trait_ids=set(executed_ids) if timing in (11,12) else None)


def attach_runtime_trait_grants(env, factory):
    """Bind the same state factory that created initial runtime trait identities."""
    env.runtime_trait_state_factory = factory
    register_on_demand_sources(getattr(env,'runtime_trait_sets',{}))


def register_on_demand_sources(groups):
    """Known on-demand classes have no persistent correction at creation.

    Activation/recipient checks remain in execution. This readiness flag does
    not authorize a conditional source or execute any effect.
    """
    from .trait_effects import KNOWN_ON_DEMAND_KINDS
    for group in groups.values():
        if not group.get('prediction') and group['values'] and all(
                trait['trait_type'] in KNOWN_ON_DEMAND_KINDS for trait in group['values']):
            group['effect_registration_complete']=True


def try_execute_after_damaged_grants(env, target):
    return try_execute_trait_grant_event(env,target,10)


def try_execute_trait_grant_event(env, target, timing):
    master = getattr(getattr(env,'rules',None),'master',None)
    groups = getattr(env,'runtime_trait_sets',None)
    factory = getattr(env,'runtime_trait_state_factory',None)
    phase_provider = getattr(env,'runtime_trait_phase_value_provider',None)
    if master is None or groups is None or factory is None or phase_provider is None:
        return False,None
    if timing==10 and not any(t['trait_type']==13 and t['action_timing']==timing for group in groups.values()
               if group['owner_unit_id']==target['id'] for t in group['values']):
        return False,None
    try:
        result = execute_owner_grants(master,factory,groups,env.units.values(),target['id'],phase_provider(),
            turn_progress_policy=getattr(env,'runtime_trait_turn_progress_policy',None),timing=timing)
    except NotImplementedError:
        return False,None
    return True,result
