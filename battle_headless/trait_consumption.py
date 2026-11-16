"""Original runtime trait counters; runtime IDs must not be master trait IDs.

This layer does not pretend to perform service-level owner/activation selection,
correction removal or IsEnabledWhileAllTraitsUsable propagation.
"""
from .original import i32


def get_limit(trait):
    base=i32(trait['base_limit'])
    return i32(base+trait.get('additional_limit',0)) if base else 0


def remaining_value(trait):return i32(get_limit(trait)-trait['used_count'])


def is_active(trait):
    return not i32(trait['base_limit']) or i32(trait['used_count'])<get_limit(trait)


def is_removable(trait):return trait['trait_type']!=114 and not is_active(trait)


def consume(trait,timing):
    matches=i32(trait['action_timing'])==i32(timing)
    if matches:trait['used_count']=i32(trait['used_count']+1)
    return matches


def consume_by_trait_id(trait_set,runtime_trait_id,phase_value,timing):
    # ConsumeByTraitId stops at the first matching runtime identity even if
    # that trait's timing does not match. Duplicate master IDs are irrelevant.
    for trait in trait_set['values']:
        if trait['runtime_trait_id']!=runtime_trait_id:continue
        consumed=consume(trait,timing)
        if consumed and timing==3:
            trait_set['turn_progress_consumed_phase_value']=phase_value
        return consumed
    return False


def remove_from_unit(trait,unit,*,force=False):
    """IngameTrait.Remove(unit,bool): registered identity -> gate -> virtual removal.

    Effects must be registered by their original subclass implementation.
    Unknown effects are never approximated by subtracting master TraitValue.
    """
    identity=trait['runtime_trait_id']
    effects=unit.get('runtime_trait_effects',{})
    if identity not in effects:return False
    effect=effects[identity]
    if not force:
        if 'can_remove' not in effect:raise NotImplementedError('Original trait CanRemove implementation required')
        if not effect['can_remove'](trait,unit):return False
    if 'remove' not in effect:raise NotImplementedError('Original trait Remove implementation required')
    effect['remove'](trait,unit)
    # Native reacquires the unit correction registry after the virtual call.
    unit['runtime_trait_effects'].pop(identity,None)
    return True


def remove_all(trait,units):
    return [unit['id'] for unit in units if remove_from_unit(trait,unit)]


def remove_set_all(trait_set,units):
    units=tuple(units)
    return [dict(runtime_trait_id=t['runtime_trait_id'],units=remove_all(t,units))
            for t in trait_set['values']]


def remove_set_from_unit(trait_set,unit):
    return [t['runtime_trait_id'] for t in trait_set['values'] if remove_from_unit(t,unit)]


def is_all_removable(trait_set):
    values=trait_set['values']
    return values is None or all(is_removable(t) for t in values)


def is_set_enabled(trait_set):
    """IngameTraitSet.IsEnabled 0x5f7f84c (distinct from Internal)."""
    values=trait_set['values']
    if not values or not trait_set['enabled_internal']:return False
    return (all if trait_set['enabled_while_all_traits_usable'] else any)(is_active(t) for t in values)


def oldest_consumable_trait_id(trait_sets,owner_id,timing):
    """0x5f8c52c: lowest signed runtime SET ID, first usable matching trait."""
    selected_set=None;selected_trait=None
    for group in trait_sets.values():
        if group['owner_unit_id']!=owner_id:continue
        identity=group['runtime_set_id']
        if selected_set is not None and identity>=selected_set:continue
        if not is_set_enabled(group):continue
        for trait in group['values']:
            if trait['action_timing']==timing and is_active(trait):
                selected_set=identity;selected_trait=trait['runtime_trait_id'];break
    return selected_trait


def consume_guaranteed_chance_step(trait_sets,units,phase_value,owner_id,timing):
    """ConsumeOnGuaranteedChanceStepUsed: timing0 no-op, single oldest ID."""
    if not timing:return None
    identity=oldest_consumable_trait_id(trait_sets,owner_id,timing)
    if identity is None:return None
    return consume_trait_count(trait_sets,units,phase_value,timing,
        owner_ids={owner_id},trait_ids={identity})


def consume_resolved_set(trait_set,selected_ids,phase_value,timing,units):
    """ConsumeTraitCount's enabled-set tail, after original owner/ID selection.

    The caller supplies selected runtime IDs and repository-ordered units.
    This does not replace the service's preceding selection/dead-owner checks.
    """
    if not trait_set['enabled_internal']:
        return dict(visited=[],removals=[],remove_set=False)
    selected_ids=set(selected_ids);units=tuple(units);visited=[];removals=[]
    force_remove_set=False
    for trait in trait_set['values']:
        identity=trait['runtime_trait_id']
        if identity not in selected_ids:continue
        consume_by_trait_id(trait_set,identity,phase_value,timing)
        visited.append(identity)
        if is_active(trait):continue
        if trait_set['enabled_while_all_traits_usable']:
            removals.extend(remove_set_all(trait_set,units))
            force_remove_set=is_removable(trait)
            break
        removals.append(dict(runtime_trait_id=identity,units=remove_all(trait,units)))
    return dict(visited=visited,removals=removals,
                remove_set=force_remove_set or is_all_removable(trait_set))


def consume_trait_count(trait_sets,units,phase_value,timing,*,owner_ids=None,
                        trait_ids=None,turn_progress_can_consume=None):
    """Native service selection/removal over explicit runtime repositories.

    Empty owner filter means all; empty trait-ID filter means none. TurnProgress
    requires its original policy service rather than a guessed phase comparison.
    """
    from .battle_sides import is_alive_on_map
    units=tuple(units);by_id={u['id']:u for u in units}
    owners=set(owner_ids or ());selected=None if trait_ids is None else set(trait_ids)
    events=[];removed=[]
    for set_id,group in list(trait_sets.items()):
        owner_id=group['owner_unit_id']
        if owners and owner_id not in owners:continue
        owner=by_id.get(owner_id)
        if owner is not None:
            existence=owner.get('existence',owner.get('chance_step',{}).get('existence'))
            if existence is None:raise NotImplementedError('Original owner existence state required')
            if not is_alive_on_map(existence):continue
        if group.get('effect_registration_complete') is False:
            raise NotImplementedError('Runtime trait state exists, but original concrete effect registration is incomplete')
        if is_all_removable(group):
            # This preclean branch calls Set.Remove once per unit, unlike
            # the exhausted all-usable tail's trait-first Set.RemoveAll.
            removals=[dict(unit_id=u['id'],runtime_trait_ids=remove_set_from_unit(group,u)) for u in units]
            events.append(dict(runtime_set_id=set_id,precleaned=True,removals=removals))
            removed.append(set_id);continue
        if not group['enabled_internal']:continue
        if timing==3:
            if turn_progress_can_consume is None:
                raise NotImplementedError('Original TurnProgress consumption policy required')
            if not turn_progress_can_consume(group,phase_value):continue
        ids=selected if selected is not None else {t['runtime_trait_id'] for t in group['values']}
        event=consume_resolved_set(group,ids,phase_value,timing,units)
        event['runtime_set_id']=set_id;events.append(event)
        if event['remove_set']:removed.append(set_id)
    for set_id in removed:trait_sets.pop(set_id,None)
    return dict(sets=events,removed_set_ids=removed)


def attach_runtime_trait_consumption(env,trait_sets,phase_value_provider,*,
                                    turn_progress_policy=None,dead_unit_ids_provider=None):
    """Attach explicit runtime instances/effect registrations to actual MAP use.

    Does not manufacture runtime IDs/activation state from master definitions.
    """
    hooks=dict(getattr(env,'map_action_lifecycle',{}))
    if 'consume_on_any_weapon_used' in hooks:
        raise ValueError('MAP weapon-use consumption lifecycle already connected')
    from .trait_turn_progress import standard_satisfies,master_league_satisfies
    if turn_progress_policy=='standard':policy=standard_satisfies
    elif turn_progress_policy=='master_league':
        policy=lambda group,current:master_league_satisfies(group,current,dead_unit_ids_provider)
    elif turn_progress_policy is None or callable(turn_progress_policy):policy=turn_progress_policy
    else:raise ValueError('Unknown original turn-progress policy')
    def consume_runtime(timing,*,owner_ids=None,trait_ids=None):
        return consume_trait_count(trait_sets,env.units.values(),phase_value_provider(),timing,
            owner_ids=owner_ids,trait_ids=trait_ids,turn_progress_can_consume=policy)
    def on_any_weapon_used(actor,weapon,event):
        event['trait_consumption']=consume_runtime(6,owner_ids={actor['id']})
    hooks['consume_on_any_weapon_used']=on_any_weapon_used
    env.map_action_lifecycle=hooks
    if policy is not None:env.runtime_trait_turn_progress_policy=policy
    env.runtime_trait_sets=trait_sets
    env.runtime_trait_phase_value_provider=phase_value_provider
    env.consume_runtime_trait_count=consume_runtime
    env.consume_runtime_guaranteed_chance_step=lambda owner,timing:consume_guaranteed_chance_step(
        trait_sets,env.units.values(),phase_value_provider(),owner['id'],timing)
    # ConsumeOnChanceStep and ConsumeOnGuaranteedChanceStepUsed have the same
    # oldest-single-ID selection and consumption flow in this pinned original.
    env.consume_runtime_chance_step=env.consume_runtime_guaranteed_chance_step
