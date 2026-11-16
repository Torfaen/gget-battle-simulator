"""Original on-demand dispatch over explicitly selected runtime traits."""
from .original import i32
from .combat_primitives import resource_percent
from .regeneration import recover_hp_by_value


def select_on_demand_traits(groups,timing,*,owner_ids=None):
    """Repository timing/class selection; usability is checked by the service.

    Dictionary group order and each group's definition order are retained.
    Empty owner selection is empty, distinct from the all-owners overload.
    """
    from .trait_effects import KNOWN_ON_DEMAND_KINDS
    owners=None if owner_ids is None else set(owner_ids)
    return [trait for group in groups.values() if owners is None or group['owner_unit_id'] in owners
            for trait in group['values'] if trait['action_timing']==timing and trait['trait_type'] in KNOWN_ON_DEMAND_KINDS]


def execute_on_demand(services,traits,timing,units,battle_context=None):
    """_ExecuteOnDemandTrait normal returns, including nullable result lists.

    Empty selection returns before constructing a context. Once a target is
    selected the list exists, even if every concrete Execute returns None.
    Only TurnProgress (3) uses the additional phase-policy gate here.
    """
    if not traits:return None
    units=tuple(units);context=services.create_context(battle_context);results=None
    for trait in traits:
        if timing==3:
            parent=services.parent(trait['runtime_trait_id'])
            if not services.turn_progress(parent,context):continue
        if not services.active(trait,context,battle_context):continue
        for unit in units:
            if not services.target(trait,unit,context,battle_context):continue
            if results is None:results=[]
            result=services.execute(trait,timing,unit)
            if result is not None:results.append(result)
    services.return_contexts()
    return results


def hp_change_rate_result(trait,target):
    """HpChangeRateTrait._GetResult: no visible limit/used-count fields."""
    if trait['trait_type']!=3:raise ValueError('HP-rate on-demand trait required')
    return dict(runtime_trait_id=trait['runtime_trait_id'],target_id=target['id'],owner_id=trait['owner_unit_id'],
        is_effective=i32(target['hp'])<i32(target['max_hp']),hp_recovery=resource_percent(target['max_hp'],i32(trait['trait_value'])),
        limit_count=0,used_count=0,show_limit=False)


def execute_hp_change_rate(trait,timing,target,*,simulation=False):
    if timing!=trait['action_timing']:return None
    result=hp_change_rate_result(trait,target)
    if not simulation:recover_hp_by_value(target,result['hp_recovery'])
    return result
