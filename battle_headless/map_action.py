"""Numeric MAP service chain, preserving explicit unported lifecycle boundaries.

MapWeaponServiceBase.Execute 0x5f5ec84 pays before executing targets; MAP
power nevertheless reads HP/EN captured by OnBattleStart before payment.
The low-level calculate/predict interfaces remain independently usable.
"""
from .map_weapon_runtime import use_map_weapon,validate_suicide_configuration
from .map_drain import calculate_drained_value,apply_drained_value
from .battle_resources import begin_battle,end_battle
from .map_target_sort import service_sort_parameters,service_sort_results


def execute(env,attacker,weapon,records,impact_coords=(),*,has_moved,
            can_move_after_any_attack,standby_terrain_type=None,step_free_move_coord=None):
    validate_suicide_configuration(weapon)
    if any(t['WeaponTraitTypeIndex']==4 for t in weapon.get('weapon_traits',[])):
        raise NotImplementedError('MAP suicide Damage routing is not connected')
    if weapon['map_range_type']==4:
        raise NotImplementedError('Moving MAP actual movement/action lifecycle is not connected')
    if weapon['map_trait_category'] not in (1,2):raise ValueError('Unknown MAP trait category')
    availability=env.map_weapon_availability(attacker,weapon,records,
        has_moved=has_moved,can_move_after_any_attack=can_move_after_any_attack,
        standby_terrain_type=standby_terrain_type)
    if not availability['available_before_check']:
        return dict(executed=False,availability=availability,fees_applied=False,action_advanced=False)
    # Validate target geometry before mutating resource state. Actual execution
    # still resolves combat context after payment, with the original snapshots.
    impacts=tuple(impact_coords)
    sort_parameters=service_sort_parameters(weapon,impacts,step_free_move_coord=step_free_move_coord,direction=attacker['direction'])
    env.map_weapon_plan(attacker,weapon,impacts)
    pending=[];trace=[];hooks=getattr(env,'map_action_lifecycle',{})
    def callback(name):
        trace.append(name)
        if name in hooks:return hooks[name](attacker,weapon,event)
        if name=='after_any_weapon_hit':
            from .trait_grants import try_execute_trait_grant_event
            handled,value=try_execute_trait_grant_event(env,attacker,13)
            if handled:
                event['after_any_weapon_hit_traits']=value
                return value
        pending.append(name)
    event=dict(executed=True,availability=availability,fees_applied=False,
               action_advanced=False,pending_lifecycle=pending,service_order=trace)
    begin_battle(attacker)
    try:
        callback('on_battle_start')
        fee=availability['costs']
        trace.append('use_map_weapon')
        from .weapon_corrections import runtime_weapon_records
        event['payment']=use_map_weapon(attacker,weapon,list(records)+runtime_weapon_records(attacker),
            **{k:fee[k] for k in ('en_cost','mp_cost','hp_cost')})
        event['fees_applied']=True
        trace.append('clear_last_main_attacked_unit_id')
        attacker['last_main_attacked_unit_id']=None
        trace.append('execute_targets')
        resolver=env.map_weapon_attack if weapon['map_trait_category']==1 else env.map_weapon_recover
        event['execution']=resolver(attacker,weapon,impacts)
        trace.append('calculate_and_apply_drain')
        event['drain']=apply_drained_value(attacker,calculate_drained_value(attacker,weapon,event['execution']['results']))
        if weapon['map_trait_category']==1 and any(row['calculation']['hit'] for row in event['execution']['results']):
            callback('after_any_weapon_hit')
        callback('consume_on_any_weapon_used')
        callback('on_battle_end')
    finally:
        end_battle(attacker)
    sort_parameters['direction']=attacker['direction']
    event['execution']['encounter_results']=event['execution']['results']
    event['execution']['results']=service_sort_results(event['execution']['results'],sort_parameters)
    event['execution']['service_sort_parameters']=sort_parameters
    # Service.Execute ends before use-case MP reflection, defeat/regeneration,
    # chance-step and activity updates. Keep that boundary visible to callers.
    pending.append('map_execute_use_case')
    return event
