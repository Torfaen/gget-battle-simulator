"""Ordinary attack/counter exchange using recovered prediction primitives.

This connects the CounterOnly subset and optional attack/counter support.
Ordinary main-attack defense substitution is optional. Imported HP barrier state
is supported; its lifecycle, full multi-hit defense and dynamic preemption remain pending.
"""
from .unit_life import is_alive
from functools import cmp_to_key
from .attack_prediction import AttackPrediction, compare_predictions


def select_counter(env, attacker, defender, *, preemptive_only=False, remaining_hp=None):
    """Predictions do not consume RNG or change actor resources."""
    rules = env.rules
    candidates = []
    for base in defender['weapons']:
        weapon = rules.effective_weapon(defender, attacker, base,
                                       action_type='Counter', opponent_action_type='Attack')
        if preemptive_only and not weapon.get('preemptive',False):
            continue
        if not env.within_range(defender, attacker, weapon):
            continue
        available = (rules.weapon_available(defender,attacker,weapon) if remaining_hp is None else
                     rules.weapon_available(defender,attacker,weapon,remaining_hp=remaining_hp))
        if not available:
            continue
        result = rules.predict_counter(defender, attacker, weapon)
        prediction = AttackPrediction(result['damage'], attacker['hp'], result['hit_rate'],
                                      weapon['en'], base.get('sort_order', 0))
        candidates.append((prediction, base))
    candidates.sort(key=cmp_to_key(lambda a, b: compare_predictions(a[0], b[0])))
    return candidates[0][1] if candidates else None


def _strike(env, attacker, target, base, *, counter=False, target_counters=False,
            action_type=None, opponent_action_type=None,geometry_target=None):
    if hasattr(env, '_refresh_state'):
        env._refresh_state()
    rules = env.rules
    action_type = action_type or ('Counter' if counter else 'Attack')
    opponent_action_type = opponent_action_type or ('Attack' if counter else ('Counter' if target_counters else 'NoAction'))
    weapon = rules.effective_weapon(attacker, target, base,
        action_type=action_type, opponent_action_type=opponent_action_type)
    context=dict(action_type=action_type,opponent_action_type=opponent_action_type)
    if geometry_target is not None:context['geometry_target']=geometry_target
    result = rules._calculate_attack(attacker, target, weapon, env.rng, prediction=False,**context)
    result.update(rules.consume_weapon(attacker, weapon))
    # AttackService._AttackByWeapon recovers after UseWeapon, before target damage.
    from .weapon_drain import apply_hp_drain,apply_en_drain
    apply_hp_drain(attacker,result)
    apply_en_drain(attacker,result)
    from .hp_barrier import apply_barrier_damage
    apply_barrier_damage(target,result)
    from .unit_life import apply_hp_damage
    applied=apply_hp_damage(target,result,result['damage'])
    from .weapon_resource_damage import apply_resource_damage
    apply_resource_damage(target,result)
    from .weapon_debuffs import grant_debuffs
    grant_debuffs(env,attacker,target,result)
    if not is_alive(target):
        if 'chance_step' in target:target['chance_step']['existence'] = 2
        if 'existence' in target:target['existence'] = 2
    result.update(applied_damage=applied, attacker=attacker['id'], target=target['id'], weapon=base['id'])
    if result['hit'] and hasattr(env,'runtime_trait_sets'):
        from .trait_grants import try_execute_trait_grant_event
        immediate=[]
        # AttackUseCase's per-weapon hit callbacks precede weapon-use consumption.
        for name,owner,timing in (('on_any_weapon_hit',attacker,12),('on_damaged',target,11)):
            handled,value=try_execute_trait_grant_event(env,owner,timing)
            immediate.append(dict(event=name,unit_id=owner['id'],handled=handled,result=value))
            if not handled:
                result.setdefault('pending_lifecycle',[]).append(dict(event=name,unit_id=owner['id']))
        result['immediate_trait_events']=immediate
    if hasattr(env,'consume_runtime_trait_count'):
        if result.get('parry'):
            result.setdefault('pending_lifecycle',[]).append(
                dict(event='consume_on_parry_success',unit_id=target['id']))
        result['runtime_weapon_use_consumption']=[dict(timing=timing,
            result=env.consume_runtime_trait_count(timing,owner_ids={attacker['id']}))
            for timing in (6,7)]
    if hasattr(env, 'skill_effect_runtime'):
        result['consumed_skill_effects'] = env.skill_effect_runtime.consume(attacker, {6, 7})
    return result


def execute_exchange(env, attacker, defender, weapon):
    # Selection is before rolls. Ordinary counters do not consume the defender's
    # phase action or movement, and never recursively provoke another counter.
    defense = None
    if hasattr(env, 'support_defense'):
        from .support_defense import select_defender, consume_defense
        defense = select_defender(env, attacker, defender, weapon)
    if hasattr(env, 'counter_policy_profile'):
        from .counter_policy import decide_response
        response = decide_response(env, attacker, defender, weapon,
                                   support_defense_enabled=defense is not None)
        counter, target_action = response['weapon'], response['action_type']
        selection_trace = response['trace']
    else:
        counter = select_counter(env, attacker, defender)
        target_action = 'Counter' if counter else 'NoAction'
        selection_trace = dict(policy='counter-only-subset', weapon=counter['id'] if counter else None)
    participants = [attacker, defender]
    supports = counter_supports = []
    if hasattr(env, 'support_exchange'):
        from .support_execution import select_support, execute_support
        supports = select_support(env, attacker, defender, target_action=target_action)
        counter_supports = select_support(env, defender, attacker, counter=True,
                                         support_defender=defense[0] if defense is not None else None)
    # Native ExecuteAsync runs a complete preemptive group before the normal
    # group; each group is support, main, counter support, counter.
    def is_preemptive(actor, target, base, action_type, opponent_type):
        return bool(env.rules.effective_weapon(actor,target,base,
            action_type=action_type,opponent_action_type=opponent_type).get('preemptive',False))
    main_preemptive = is_preemptive(attacker,defender,weapon,'Attack',target_action)
    counter_preemptive = (is_preemptive(defender,attacker,counter,'Counter','Attack')
                          if counter is not None else False)
    support_groups = {phase: [] for phase in (True,False)}
    counter_groups = {phase: [] for phase in (True,False)}
    for candidate in supports:
        support_groups[is_preemptive(candidate[0],defender,candidate[1],
            'SupportAttack',target_action)].append(candidate)
    for candidate in counter_supports:
        counter_groups[is_preemptive(candidate[0],attacker,candidate[1],
            'SupportCounter','Attack')].append(candidate)
    sequence=[];support_attacks=[];support_counters=[]
    damaged_by_side={1:{},2:{}};hit_owners_by_side={1:{},2:{}}
    main_result=counter_result=None
    main_skipped=counter_skipped=None
    for phase in (True,False):
        def strike(*args, **kwargs):
            event=_strike(*args,**kwargs)
            event['preemptive']=phase
            if event.get('executed',True) and event['hit']:
                side=1 if kwargs.get('counter') or kwargs.get('action_type')=='SupportCounter' else 2
                damaged_by_side[side].setdefault(event['target'],env.units[event['target']])
                hit_owners_by_side[3-side].setdefault(event['attacker'],env.units[event['attacker']])
            sequence.append(dict(attacker=event['attacker'],target=event['target'],
                weapon=event['weapon'],preemptive=phase,
                action_type=kwargs.get('action_type') or ('Counter' if kwargs.get('counter') else 'Attack')))
            return event
        if hasattr(env,'support_exchange'):
            events,used=execute_support(env,attacker,defender,support_groups[phase],strike,
                                       target_action=target_action)
            support_attacks.extend(events);participants.extend(used)
        if main_preemptive == phase:
            if not is_alive(attacker) or not is_alive(defender):
                main_skipped='participant_defeated'
            elif defense is None:
                main_result=strike(env,attacker,defender,weapon,opponent_action_type=target_action)
            else:
                guard,ratio,_=defense
                main_result=strike(env,attacker,guard,weapon,opponent_action_type='SupportDefense',geometry_target=defender)
                main_result['support_defense']=consume_defense(guard,ratio)
                if hasattr(env,'support_policy_profile'):
                    from .support_ai import support_defense_policy
                    main_result['support_defense']['policy']=support_defense_policy(env,guard,defender)
                timing=main_result['support_defense']['consumed_timing']
                if timing and hasattr(env,'skill_effect_runtime'):
                    main_result['support_defense']['consumed_skill_effects']=env.skill_effect_runtime.consume(guard,{timing})
                main_result['protected_target']=defender['id'];participants.append(guard)
        if hasattr(env,'support_exchange'):
            events,used=execute_support(env,defender,attacker,counter_groups[phase],strike,counter=True)
            support_counters.extend(events);participants.extend(used)
        if counter is not None and counter_preemptive == phase:
            if not is_alive(defender) or not is_alive(attacker):
                counter_skipped='participant_defeated'
            else:
                if hasattr(env,'_refresh_state'):env._refresh_state()
                effective=env.rules.effective_weapon(defender,attacker,counter,
                    action_type='Counter',opponent_action_type='Attack')
                if env.rules.weapon_available(defender,attacker,effective):
                    counter_result=strike(env,defender,attacker,counter,counter=True)
                else:
                    counter_skipped='weapon_unavailable_after_attack'
    # Keep the action event schema stable even when a preemptive response kills
    # the attacker before its weapon executes. A skipped attack has no RNG/cost.
    result=main_result if main_result is not None else dict(hit=False,critical=False,
        damage=0,applied_damage=0,attacker=attacker['id'],target=defender['id'],
        weapon=weapon['id'],executed=False)
    if main_skipped:result['main_attack_skipped']=main_skipped
    result['counter_selection']=selection_trace
    result['counter']=counter_result
    if counter_skipped:result['counter_skipped']=counter_skipped
    result['battle_order']=('preemptive-groups-before-normal-groups' if any(e['preemptive'] for e in sequence)
                            else 'support-main-counter-support-counter')
    result['attack_sequence']=sequence
    if hasattr(env,'support_exchange'):
        result['support_attacks']=support_attacks
        result['support_counters']=support_counters
    # AttackUseCase.ExecuteAsync: side2 damaged set (0x5acbd80), then side1
    # (0x5acbf3c), after every strike and before evasion/MP reflection. Hits
    # enter these sets even when the HP damage is zero; deduplicate per side.
    if hasattr(env,'runtime_trait_sets'):
        from .trait_grants import try_execute_after_damaged_grants,try_execute_trait_grant_event
        from .regeneration import try_execute_after_damaged_recovery
        hit_events=[]
        # Native 0x5acb5ec / 0x5acb998: attacking-side then defending-side hit owners.
        for side in (1,2):
            for owner in hit_owners_by_side[side].values():
                handled,value=try_execute_trait_grant_event(env,owner,13)
                hit_events.append(dict(unit_id=owner['id'],side=side,handled=handled,result=value))
                if not handled:
                    result.setdefault('pending_lifecycle',[]).append(
                        dict(event='on_battle_end_after_any_weapon_hit',unit_id=owner['id']))
        result['battle_end_hit_traits']=hit_events
        events=[]
        for side in (2,1):
            for target in damaged_by_side[side].values():
                handled,value=try_execute_after_damaged_grants(env,target)
                if not handled:handled,value=try_execute_after_damaged_recovery(env,target)
                events.append(dict(unit_id=target['id'],side=side,handled=handled,result=value))
                if not handled:
                    result.setdefault('pending_lifecycle',[]).append(
                        dict(event='on_battle_end_after_damaged',unit_id=target['id']))
        result['battle_end_damaged_traits']=events
    # Reflect accumulated costs once per participant after all weapon uses.
    for unit in {u['id']: u for u in participants}.values():
        env.rules.reflect_battle_mp(unit)
    if hasattr(env, 'skill_effect_runtime'):
        # Native AttackUseCase accumulates side evasion flags and consumes once
        # only after the entire exchange, preserving protection through supports.
        strikes = [result, *result.get('support_attacks', []),
                   *result.get('support_counters', [])]
        if result['counter'] is not None:
            strikes.append(result['counter'])
        evaded = {strike['target'] for strike in strikes if strike.get('executed',True) and not strike['hit']}
        consumed = []
        for unit in (attacker, defender):
            if unit['id'] in evaded:
                entries = env.skill_effect_runtime.consume(unit, {9}, first_only=True)
                if entries:
                    consumed.append(dict(unit=unit['id'], effects=entries))
        result['battle_end_evasion_effects'] = consumed
    return result


def attach_counter_exchange(env):
    required = ('effective_weapon', 'weapon_available', 'predict_counter',
                '_calculate_attack', 'consume_weapon', 'reflect_battle_mp')
    if not all(hasattr(env.rules, name) for name in required):
        raise TypeError('Counter exchange requires controlled original rules')
    env._execute_exchange = lambda attacker, defender, weapon: execute_exchange(env, attacker, defender, weapon)
    env.counter_exchange = 'counter-only-subset'
