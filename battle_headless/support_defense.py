"""Ordinary main-attack defense substitution with native total-HP scoring.

Candidate reach uses the existing uniform-cost map adapter; full native terrain
pathfinding, AI policy, shield/trait reduction and multi-hit coverage are pending.
"""
from .unit_life import is_alive
from dataclasses import asdict
from functools import cmp_to_key
import math
from .mechanics import SupportBudget
from .original import div, i32
from .support_execution import support_priority_key


def remaining_total_hp_rate(unit,prediction):
    from .weapon_power_boosts import ieee_div
    hp=i32(unit['hp']-prediction.get('hp_damage',prediction['damage']))
    barrier=i32(unit.get('hp_barrier',0)-prediction.get('hp_barrier_damage',0)) if hp>0 else 0
    return ieee_div(i32(hp+barrier),i32(unit['max_hp']+unit.get('max_hp_barrier',0)))

def compare_defenders(first,second):
    # Native second.Single.CompareTo(first), then ascending signed native ID.
    a,b=first[1],second[1]
    if b<a:return -1
    if b>a:return 1
    if math.isnan(b) and not math.isnan(a):return -1
    if math.isnan(a) and not math.isnan(b):return 1
    left,right=i32(first[2]),i32(second[2])
    return (left>right)-(left<right)


def select_defender(env, attacker, protected, base):
    if not is_alive(protected):
        return None
    target_cells = env.battle_map.occupied(protected)
    candidates = []
    for order, unit in enumerate(env.units.values()):
        if unit['id'] == protected['id'] or unit['team'] != protected['team'] or not is_alive(unit):
            continue
        if hasattr(env,'support_policy_profile'):
            from .support_ai import support_defense_policy
            if not support_defense_policy(env,unit,protected)['allowed']:continue
        corrections = unit.get('scalar_corrections', {})
        if corrections.get('ActionInabilityCount', 0) > 0 or corrections.get('MovementInabilityCount', 0) > 0:
            continue
        if not SupportBudget(**unit.get('support_budget', {})).can_defend():
            continue
        if not env.battle_map.support_defend_coords(unit).intersection(target_cells):
            continue
        weapon = env.rules.effective_weapon(attacker, unit, base,
            action_type='Attack', opponent_action_type='SupportDefense')
        if not env.rules.weapon_available(attacker, unit, weapon):
            continue
        prediction = env.rules._calculate_attack(attacker, unit, weapon, None, prediction=True,
            action_type='Attack', opponent_action_type='SupportDefense',geometry_target=protected)
        ratio = remaining_total_hp_rate(unit,prediction)
        numeric_id = support_priority_key(unit, 0, order)[1]
        candidates.append((unit, ratio, numeric_id))
    candidates.sort(key=cmp_to_key(compare_defenders))
    return candidates[0] if candidates else None


def consume_defense(unit, predicted_remaining_ratio):
    budget = SupportBudget(**unit['support_budget'])
    timing = budget.consume('defense')
    unit['support_budget'] = asdict(budget)
    return dict(unit=unit['id'], predicted_remaining_ratio=predicted_remaining_ratio,
                consumed_timing=timing, support_budget=asdict(budget),
                profile='ordinary-main-attack-defense-subset')


def attach_support_defense(env):
    if not hasattr(env, 'battle_map') or not hasattr(env, '_execute_exchange'):
        raise ValueError('Support defense requires map and counter exchange adapters')
    env.rules._battle_distance=env.battle_map.distance
    env.support_defense = 'ordinary-main-attack-defense-subset'
