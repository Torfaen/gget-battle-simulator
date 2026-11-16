"""Resolved original RegenerationTrait execution (0x5fb6c70/0x5fb6d4c).

Recipient/activation/timing selection belongs to the trait service. This module
does not invoke a regeneration merely because a master definition exists.
"""
from .original import i32
from .combat_primitives import resource_percent
from .trait_consumption import get_limit
from .trait_factory import i64
from .trait_turn_progress import standard_satisfies


def recover_hp_by_value(unit,value,*,predicting=False):
    """RecoverHpByValue 0x613b248; return requested value, track actual healing."""
    value=i32(value)
    if predicting:return value
    before=min(unit['hp'],unit['max_hp'])
    unit['hp']=min(unit['max_hp'],i32(before+value))
    healed=max(0,i32(unit['hp']-before))
    unit['current_battle_total_heal_taken']=i64(unit.get('current_battle_total_heal_taken',0)+healed)
    return value


def resolved_result(trait,target):
    if trait['trait_type']!=83:raise ValueError('Regeneration trait required')
    return dict(runtime_trait_id=trait['runtime_trait_id'],target_id=target['id'],
        owner_id=trait['owner_unit_id'],is_effective=target['hp']<target['max_hp'],
        hp_recovery=resource_percent(target['max_hp'],i32(trait['trait_value'])),
        limit_count=get_limit(trait),used_count=i32(trait['used_count']),show_limit=True)


def execute_resolved(trait,target,*,simulation=False):
    """OnSimulate returns the result; OnExecute also applies its full HP value.

    Even ineffective/full-HP results execute recovery; boostCount is ignored by
    the original override. Count consumption is a separate service operation.
    """
    result=resolved_result(trait,target)
    if not simulation:recover_hp_by_value(target,result['hp_recovery'])
    return result


def _prepare_owner_regeneration(master,trait_sets,owner,current_phase,turn_progress_policy,*,timing=14):
    """Supported unconditional owner recovery selection for timing10/14.

    Preflight every owner group before healing. Unsupported mixed on-demand
    groups, activation or recipient predicates remain explicit, not ignored.
    The result is numeric; original visual accumulator DTOs are not reproduced.
    """
    from .trait_consumption import is_active
    from .weapon_corrections import _conditions
    prepared=[];selected=[]
    for group in trait_sets.values():
        if group['owner_unit_id']!=owner['id']:continue
        values=group['values'] or []
        if timing!=14 and any(t['action_timing']==timing and t['trait_type']!=83 for t in values):
            raise NotImplementedError('Other on-demand effect execution not connected')
        regeneration=[t for t in values if t['action_timing']==timing and t['trait_type']==83]
        if not regeneration:
            if group.get('effect_registration_complete') is False:
                raise NotImplementedError('Other owner group effect registration required before consumption')
            continue
        if any(t['trait_type']!=83 for t in values):
            raise NotImplementedError('Mixed on-demand group activation not connected')
        detail=group['master_detail']
        if any(detail.get(key) for key in ('DuplicationGroupId','IsStackable','IsEnabledWithGrantOwnerActive')):
            raise NotImplementedError('Regeneration group arbitration not connected')
        if group.get('prediction'):raise NotImplementedError('Prediction owner repository required')
        for trait in values:
            row=trait['master_trait']
            if any(row[key] for key in ('ActiveConditionSetId','ValueSourceConditionSetId',
                    'TraitBoostConditionSetId','TargetTraitDuplicationGroupId','TargetWeaponConditionId')):
                raise NotImplementedError('Conditional regeneration not connected')
            targets=_conditions(master,row['TargetConditionSetId'])
            if len(targets)!=1 or targets[0]['TargetTypes']!='Owner' or any(v for k,v in targets[0].items()
                    if k not in {'Id','TraitConditionSetId','GroupId','TargetTypes'}):
                raise NotImplementedError('Regeneration recipient selection not connected')
        prepared.append(group)
        if group['enabled_internal'] and turn_progress_policy(group,current_phase):
            selected.extend(t for t in regeneration if is_active(t))
    if selected:
        existence=owner.get('existence',owner.get('chance_step',{}).get('existence'))
        if existence not in (0,1):
            raise NotImplementedError('Live owner existence required for regeneration subset')
        # Validate every result input before marking any group registered or
        # healing a preceding participant in the MAP stage.
        for trait in selected:resolved_result(trait,owner)
    return prepared,selected


def execute_owner_regeneration(master,trait_sets,units,owner,current_phase,*,turn_progress_policy=standard_satisfies,timing=14):
    from .trait_consumption import consume_trait_count
    if timing not in (10,14):raise NotImplementedError('Owner recovery service timing not ported')
    units=tuple(units)
    prepared,selected=_prepare_owner_regeneration(master,trait_sets,owner,current_phase,turn_progress_policy,timing=timing)
    # On-demand regeneration has no persistent correction to install; the
    # validated state can enter the existing consume/cleanup service.
    for group in prepared:group['effect_registration_complete']=True
    results=[execute_resolved(t,owner) for t in selected]
    consumption=None
    if results or timing==10:
        consumption=consume_trait_count(trait_sets,units,current_phase,timing,owner_ids={owner['id']})
    return dict(results=results,consumption=consumption)


def execute_map_regeneration_stage(env,master,trait_sets,attacker,event,current_phase):
    """Original MAP regeneration stage, called after battle-end traits.

    Actor first, then sorted DTO targets, identity-deduplicated; distinct from
    the target-first resource-reflection phase. Does not advance later stages.
    """
    if not event.get('executed') or 'service_sort_parameters' not in event.get('execution',{}):
        raise ValueError('Completed sorted MAP service result required')
    if 'regeneration' in event.get('post_battle_stages',{}):
        raise ValueError('MAP regeneration stage already executed')
    ordered=[attacker];seen={attacker['id']}
    for row in event['execution']['results']:
        target=env.units.get(row['target'])
        if target is not None and target['id'] not in seen:
            seen.add(target['id']);ordered.append(target)
    turn_progress_policy=getattr(env,'runtime_trait_turn_progress_policy',standard_satisfies)
    # Unsupported later participants cannot leave earlier recipients healed.
    for owner in ordered:_prepare_owner_regeneration(master,trait_sets,owner,current_phase,turn_progress_policy)
    results=[dict(unit_id=owner['id'],**execute_owner_regeneration(
        master,trait_sets,env.units.values(),owner,current_phase,turn_progress_policy=turn_progress_policy)) for owner in ordered]
    event.setdefault('post_battle_stages',{})['regeneration']=results
    return results


def try_execute_after_damaged_recovery(env,target):
    """Try supported timing10 recovery; leave unsupported concrete effects pending."""
    master=getattr(getattr(env,'rules',None),'master',None)
    groups=getattr(env,'runtime_trait_sets',None)
    phase_provider=getattr(env,'runtime_trait_phase_value_provider',None)
    if master is None or groups is None or phase_provider is None:return False,None
    current=phase_provider();policy=getattr(env,'runtime_trait_turn_progress_policy',standard_satisfies)
    try:_prepare_owner_regeneration(master,groups,target,current,policy,timing=10)
    except NotImplementedError:return False,None
    return True,execute_owner_regeneration(master,groups,env.units.values(),target,current,
        turn_progress_policy=policy,timing=10)
