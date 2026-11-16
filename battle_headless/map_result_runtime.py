"""Original MAP result application; scalar DTO inputs are already calculated.

Attack formula/context generation and complete trait callbacks remain separate.
Prediction applies to a shadow unit, matching the native prediction-state boundary.
"""
from copy import deepcopy
from .original import i32
from .combat_primitives import resource_percent


def hit_value(result,field):
    value=result.get(field)
    return None if value is None else i32(value) if result['hit'] else 0


def result_operations(result,*,prediction=False,can_escape=False):
    if not result['hit']:return [('attack_evaded',)]
    if result['is_attack']:
        operations=[('damage',i32(result.get('hp_damage') or 0),i32(result.get('hp_barrier_damage') or 0),bool(can_escape))]
        if not prediction:operations.extend([('remove_damage_barrier',),('remove_damage_cut',)])
    else:operations=[('recover_hp',i32(-(result.get('hp_damage') or 0)))]
    operations.extend([('recover_en',i32(-(hit_value(result,'en_damage') or 0))),
                       ('queue_mp',i32(-(hit_value(result,'mp_damage') or 0)))])
    return operations


def recovery_result(target,*,hp_rate,en_rate,mp_value):
    # CalculateMapWeaponBattleResult recovery branch does not multiply hitCount.
    from .weapon_resource_damage import mp_change_enabled
    if mp_value is not None and not mp_change_enabled(target):mp_value=0
    return dict(hit=True,is_attack=False,hit_rate=100,critical_type=0,
                hp_damage=i32(-resource_percent(target['max_hp'],hp_rate)) if hp_rate else None,
                hp_barrier_damage=None,
                en_damage=i32(-resource_percent(target['max_en'],en_rate)) if en_rate else None,
                mp_damage=i32(-mp_value) if mp_value is not None else None)


def apply_result(target,result,*,prediction=False,can_escape=False,lifecycle=None):
    from .hp_barrier import apply_barrier_damage,remove_broken_barrier
    from .unit_life import apply_hp_damage,is_alive
    from .weapon_resource_damage import plus_en,mp_change_enabled
    if can_escape and result['is_attack'] and result['hit']:
        raise NotImplementedError('MAP escape/defeat routing is not connected')
    unit=deepcopy(target) if prediction else target
    operations=result_operations(result,prediction=prediction,can_escape=can_escape)
    before=deepcopy(unit);pending=[];callbacks=lifecycle or {}
    for operation in operations:
        kind=operation[0]
        if kind=='damage':
            applied=dict(hit=True,hp_damage=operation[1],hp_barrier_damage=operation[2])
            apply_barrier_damage(unit,applied);apply_hp_damage(unit,applied,operation[1])
            if not is_alive(unit):
                if 'existence' in unit:unit['existence']=2
                if 'chance_step' in unit:unit['chance_step']['existence']=2
        elif kind=='recover_hp':
            from .regeneration import recover_hp_by_value
            recover_hp_by_value(unit,operation[1])
            if unit['hp']>0:unit.pop('zero_hp_survival',None)
        elif kind=='recover_en':plus_en(unit,operation[1])
        elif kind=='queue_mp':
            delta=operation[1] if mp_change_enabled(unit) else 0
            unit['pending_mp_change']=i32(unit.get('pending_mp_change',0)+delta)
        elif kind in callbacks:callbacks[kind](unit)
        elif kind=='remove_damage_barrier':
            if unit.get('hp_barriers'):remove_broken_barrier(unit,unit['hp_barriers'][0]['instance_id'])
        else:pending.append(kind)
    return dict(prediction=prediction,operations=operations,before=before,after=deepcopy(unit),pending_lifecycle=pending)


def resolve_recovery(env,attacker,weapon,impact_coords=(),*,prediction=False):
    from .battle_resources import target_battle_resources
    if weapon['map_trait_category']!=2:raise ValueError('Recovery MAP category required')
    plan=env.map_weapon_plan(attacker,weapon,impact_coords)
    results=[]
    for row in plan['targets']:
        target=env.units[row['target']]
        with target_battle_resources(attacker,target,prediction=prediction):
            effective=weapon
            if 'map_level_stats' in weapon:
                from .map_weapon_data import recovery_rate
                from .weapon_corrections import select_combat_corrections,runtime_weapon_records,sum_field
                master=getattr(env.rules,'master',None)
                if master is None:
                    if attacker.get('combat_traits'):
                        raise NotImplementedError('Master data required for recovery trait selection')
                    records=runtime_weapon_records(attacker)
                else:
                    records=select_combat_corrections(master,attacker,target,weapon,
                        'Attack','NoAction',attacker['id'])['weapon']
                    from .weapon_passives import corrections
                    records+=corrections(master,attacker,target,weapon,'Attack','NoAction')['weapon']
                effective=dict(weapon)
                for field,key,limit in [('hp_recovery_rate','HpRecoveryRate',100),
                                        ('en_recovery_rate','EnRecoveryRate',100),
                                        ('mp_recovery_value','MpRecoveryValue',12)]:
                    base=weapon['map_level_stats'].get(field,weapon.get(field))
                    effective[field]=recovery_rate(base or 0,sum_field(records,key,weapon),limit) if base else (None if field=='mp_recovery_value' else 0)
            calculated=recovery_result(target,hp_rate=effective['hp_recovery_rate'],en_rate=effective['en_recovery_rate'],mp_value=effective['mp_recovery_value'])
            application=apply_result(target,calculated,prediction=prediction,lifecycle=getattr(env,'map_result_lifecycle',None))
            from .map_action_mp import resolve_target_after_result
            after_result=resolve_target_after_result(env,attacker,target,weapon,calculated,prediction=prediction)
        results.append(dict(target=row['target'],target_coord=list(row['target_coord']),hit_count=row['hit_count'],calculation=calculated,application=application,battle_action_mp_events=after_result['mp_events'],after_result=after_result))
    return dict(plan=plan,results=results,prediction=prediction,fees_applied=False,action_advanced=False)


def attach_map_result_runtime(env):
    env.map_result_profile='native-map-resource-application-subset'
    env.map_apply_result=lambda target,result,prediction=False:apply_result(target,result,prediction=prediction,lifecycle=getattr(env,'map_result_lifecycle',None))
    env.map_weapon_recover=lambda attacker,weapon,impact_coords=(),prediction=False:resolve_recovery(env,attacker,weapon,impact_coords,prediction=prediction)
    from .map_attack import attach_map_attack
    attach_map_attack(env)
