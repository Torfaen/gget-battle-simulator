"""MAP executor MP events, from 0x5f4a7d4..0x5f4ad58.

Full trait callback ordering remains external. Events here match the current
resolved target state; later resurrection/escape callbacks must precede kill.
"""
from .original import i32,mul,div,round_down
from .weapon_resource_damage import mp_change_enabled
from .unit_life import is_alive


def battle_action_delta(unit,value):
    rate=unit.get('scalar_corrections',{}).get('MpFluctuationRate',0)
    return i32(i32(value)+round_down(div(mul(i32(value),i32(rate)),100)))


def queue_battle_action_mp(unit,value,*,attack_failure=False):
    if attack_failure and unit.get('scalar_corrections',{}).get('AntiAttackEvadedMpReductionCount',0)>0:
        return 0
    delta=battle_action_delta(unit,value)
    if not mp_change_enabled(unit):return 0
    unit['pending_mp_change']=i32(unit.get('pending_mp_change',0)+delta)
    return delta


def apply_map_mp_events(attacker,target,result,coefficients,*,include_outcome=True,include_kill=True):
    events=[]
    def apply(unit,kind,*,failure=False):
        value=queue_battle_action_mp(unit,coefficients[kind],attack_failure=failure)
        events.append(dict(unit_id=unit['id'],coefficient=kind,queued_mp=value))
    if include_outcome and result['hit']:
        apply(attacker,9)
        # Native HitHpTotalDamage includes HP barrier damage and requires >0.
        total=i32((result.get('hp_damage') or 0)+(result.get('hp_barrier_damage') or 0))
        if total>0:apply(target,13)
    elif include_outcome:
        apply(target,10);apply(attacker,11,failure=True)
    if include_kill and not is_alive(target):apply(attacker,12)
    return events


def apply_registered_map_mp_events(env,attacker,target,result,*,prediction=False,include_outcome=True,include_kill=True):
    master=getattr(getattr(env,'rules',None),'master',None)
    if prediction or master is None or 'm_ingame_coefficient_config' not in master.tables:return None
    coefficients={r['IngameCoefficientConfigTypeIndex']:r['Value']
                  for r in master.tables['m_ingame_coefficient_config']}
    return apply_map_mp_events(attacker,target,result,coefficients,include_outcome=include_outcome,include_kill=include_kill)


def resolve_target_after_result(env,attacker,target,weapon,result,*,prediction=False):
    """Executor's per-target MP/trait order before target OnBattleEnd.

    Missing concrete trait services remain pending; callback hooks must execute
    original implementations, not guessed substitutes. Prediction invokes none.
    """
    if prediction:return dict(mp_events=None,order=[],pending_lifecycle=[])
    hooks=getattr(env,'map_target_lifecycle',{});pending=[];order=['outcome_mp'];callback_results={}
    events=apply_registered_map_mp_events(env,attacker,target,result,include_kill=False)
    if events is None:pending.append('outcome_mp')
    def callback(name):
        order.append(name)
        if name in hooks:callback_results[name]=hooks[name](attacker,target,weapon,result)
        elif name in ('on_any_weapon_hit','on_damaged'):
            from .trait_grants import try_execute_trait_grant_event
            owner,timing=(attacker,12) if name=='on_any_weapon_hit' else (target,11)
            handled,value=try_execute_trait_grant_event(env,owner,timing)
            if handled:callback_results[name]=value
            else:pending.append(name)
        elif name=='on_battle_end_after_damaged':
            from .trait_grants import try_execute_after_damaged_grants
            from .regeneration import try_execute_after_damaged_recovery
            handled,value=try_execute_after_damaged_grants(env,target)
            if not handled:handled,value=try_execute_after_damaged_recovery(env,target)
            if handled:callback_results[name]=value
            else:pending.append(name)
        elif name=='consume_on_battle_end_after_evasion':
            from .trait_consumption import oldest_consumable_trait_id
            legacy=getattr(env,'skill_effect_runtime',None)
            legacy_active=any(e['action_timing']==9 and e['remaining']>0
                              for e in target.get('active_skill_effects',[]))
            runtime_id=oldest_consumable_trait_id(getattr(env,'runtime_trait_sets',{}),target['id'],9)
            if legacy_active and runtime_id is not None:
                pending.extend([name,'mixed_runtime_legacy_evasion_selection'])
            elif legacy_active and legacy is not None:
                callback_results[name]=legacy.consume(target,{9},first_only=True)
            elif hasattr(env,'consume_runtime_chance_step'):
                callback_results[name]=env.consume_runtime_chance_step(target,9)
            elif legacy is not None:callback_results[name]=[]
            else:pending.append(name)
        else:pending.append(name)
    if result['hit']:
        if result.get('evaluated_debuffs'):callback('grant_weapon_debuff')
        if result['is_attack']:
            for name in ('on_any_weapon_hit','on_damaged','on_battle_end_after_damaged'):callback(name)
    else:callback('consume_on_battle_end_after_evasion')
    order.append('kill_mp')
    kill=apply_registered_map_mp_events(env,attacker,target,result,include_outcome=False)
    if kill is None:pending.append('kill_mp')
    elif events is not None:events.extend(kill)
    return dict(mp_events=events,order=order,pending_lifecycle=pending,callback_results=callback_results)
