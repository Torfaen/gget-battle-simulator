"""MAP service drain fold (CalculateDrainedValue 0x5f5f904).

Inputs are executor calculation rows, not observed HP/EN deltas. Barrier
damage contributes to HP drain; overkill and resource clamps do not cap it.
"""
from .original import i32
from .map_result_runtime import hit_value
from .weapon_drain import first_drain,apply_hp_drain,apply_en_drain


def damage_totals(results):
    hp=en=0
    for row in results:
        result=row['calculation']
        # Recovery DTOs fill Recovery rather than Damage in change-value DTOs.
        if not result['is_attack']:continue
        hp=i32(i32(hp+(hit_value(result,'hp_damage') or 0))+
               (hit_value(result,'hp_barrier_damage') or 0))
        en=i32(en+(hit_value(result,'en_damage') or 0))
    return hp,en


def calculate_drained_value(attacker,weapon,results):
    results=list(results)
    if not results:
        # Native returns BattleDrainedInfo.None before querying traits/maxima.
        return dict(total_hp_damage=0,total_en_damage=0,drained_hp=None,drained_en=None)
    hp,en=damage_totals(results)
    traits=weapon.get('weapon_traits',[])
    return dict(total_hp_damage=hp,total_en_damage=en,
        drained_hp=first_drain(traits,10,hp,attacker['max_hp']),
        drained_en=first_drain(traits,11,en,attacker['max_en']))


def apply_drained_value(attacker,drained,*,prediction=False):
    from copy import deepcopy
    unit=deepcopy(attacker) if prediction else attacker
    result=dict(drained)
    apply_hp_drain(unit,result);apply_en_drain(unit,result)
    return result
