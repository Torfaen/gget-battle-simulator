"""Attack/counter support subset; full native policy and scheduler are pending."""
from .unit_life import is_alive
from dataclasses import asdict
from functools import cmp_to_key
from .mechanics import SupportBudget
from .attack_prediction import AttackPrediction, compare_predictions
from .original import f32, mul, i32

# Both native selectors use enabled_count <= 2 before enabling the next unit.
MAX_SUPPORT_UNITS = 3


def support_priority_key(unit, predicted_damage, fallback_order):
    """Native descending int damage, ascending signed IngameUnitId.ToInt.

    The explicit local-order fallback is used only when original numeric IDs
    are absent; user-unit IDs and map NPC IDs are not interchangeable with it.
    """
    native_id = unit.get('ingame_id', fallback_order)
    if type(native_id) is not int:
        raise ValueError('ingame_id must be an original numeric instance ID')
    return -i32(predicted_damage), i32(native_id)


def choose_support_weapon(env, unit, target, *, counter=False, target_action='Counter'):
    action_type = 'SupportCounter' if counter else 'SupportAttack'
    opponent_type = 'Attack' if counter else target_action
    candidates = []
    for base in unit['weapons']:
        weapon = env.rules.effective_weapon(unit, target, base,
            action_type=action_type, opponent_action_type=opponent_type)
        if not env.within_range(unit, target, weapon) or not env.rules.weapon_available(unit, target, weapon):
            continue
        result = env.rules._calculate_attack(unit, target, weapon, None, prediction=True,
            action_type=action_type, opponent_action_type=opponent_type)
        score = AttackPrediction(result['damage'], target['hp'], result['hit_rate'],
                                 weapon['en'], base.get('sort_order', 0))
        candidates.append((score, base, result, weapon))
    candidates.sort(key=cmp_to_key(lambda a, b: compare_predictions(a[0], b[0])))
    if not candidates:
        return None
    # SupportWeaponSelector.TrySelect 0x622d630: first prioritized prediction
    # costing at most float32(maxEN)*0.25, else first prioritized prediction.
    threshold = mul(f32(unit['max_en']), 0.25)
    return next((c for c in candidates if f32(c[3]['en']) <= threshold), candidates[0])


def select_support(env, main, target, *, counter=False, target_action='Counter', support_defender=None):
    candidates = []
    for order, unit in enumerate(env.units.values()):
        if unit['id'] in (main['id'], target['id']) or unit['team'] != main['team'] or not is_alive(unit):
            continue
        # CounterSupportService reads the current step's EnabledOnly guard,
        # then compares its unit ID before considering side, AI or weapons.
        if counter and support_defender is not None and unit['id']==support_defender['id']:
            continue
        if hasattr(env,'support_policy_profile'):
            from .support_ai import support_action_policy
            if not support_action_policy(env,unit,main)['allowed']:
                continue
        corrections = unit.get('scalar_corrections', {})
        if corrections.get('ActionInabilityCount', 0) > 0 or corrections.get('AttackInabilityCount', 0) > 0:
            continue
        if not SupportBudget(**unit.get('support_budget', {})).can_attack():
            continue
        selected = choose_support_weapon(env, unit, target, counter=counter, target_action=target_action)
        if selected is not None:
            candidates.append((unit, selected[1], selected[2]['damage'], order))
    candidates.sort(key=lambda c: support_priority_key(c[0], c[2], c[3]))
    # CounterSupportSelector.Select's direct trampoline has also been followed;
    # its body at 0x5ef8148 uses the same three-unit limit.
    return candidates[:MAX_SUPPORT_UNITS]


def execute_support(env, main, target, selected, strike, *, counter=False, target_action='Counter'):
    events = []
    participants = []
    if not is_alive(main):
        return events, participants
    for unit, base, predicted_damage, _ in selected:
        if not is_alive(target):
            break
        if not is_alive(unit):
            continue
        budget = SupportBudget(**unit.get('support_budget', {}))
        if not budget.can_attack():
            continue
        action_type = 'SupportCounter' if counter else 'SupportAttack'
        opponent_type = 'Attack' if counter else target_action
        if hasattr(env, '_refresh_state'):
            env._refresh_state()
        weapon = env.rules.effective_weapon(unit, target, base,
            action_type=action_type, opponent_action_type=opponent_type)
        if not env.within_range(unit, target, weapon) or not env.rules.weapon_available(unit, target, weapon):
            continue
        event = strike(env, unit, target, base,
                       action_type=action_type, opponent_action_type=opponent_type)
        timing = budget.consume('attack')
        unit['support_budget'] = asdict(budget)
        if timing and hasattr(env, 'skill_effect_runtime'):
            event.setdefault('consumed_skill_effects', []).extend(env.skill_effect_runtime.consume(unit, {timing}))
        event.update(support_kind=action_type, consumed_timing=timing,
                     predicted_damage=predicted_damage, support_budget=asdict(budget))
        if hasattr(env,'support_policy_profile'):
            from .support_ai import support_action_policy
            event['support_policy']=support_action_policy(env,unit,main)
        events.append(event)
        participants.append(unit)
    return events, participants


def attach_support_exchange(env):
    if not hasattr(env, '_execute_exchange'):
        raise ValueError('Attach counter exchange first')
    env.support_exchange = 'attack-counter-support-subset'
