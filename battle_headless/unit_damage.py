"""Actual (non-prediction) IngameUnitEntity damage and evasion mutations.

Normal DamageApplyInfo only. Escape form changes, skill removal and subsequent
battle callbacks belong to their callers. Numeric counters retain CLR widths.
"""
from .original import i32
from .trait_factory import i64


def damage_cut_active(unit):
    correction=unit.get('damage_cut_correction')
    if correction is None:
        if unit.get('damage_cut'):raise NotImplementedError('Import the limited DamageCut correction')
        return False
    return i32(correction['remaining_count'])>0


def is_unbreakable(unit):
    return i32(unit.get('scalar_corrections',{}).get('UnbreakableCount',0))>0


def is_broken(unit):
    return not is_unbreakable(unit) and i32(unit['hp'])<=0


def plus_hp(unit,value):
    # _PlusHp clamps the input and the result to the upper bound only.
    unit['hp']=min(unit['max_hp'],i32(min(unit['hp'],unit['max_hp'])+value))


def evade_attack(unit,*,predicting=False):
    if not predicting:unit['received_attack_count']=i32(unit.get('received_attack_count',0)+1)


def damage_normal(unit,hp_damage,barrier_damage,can_escape=False,*,predicting=False):
    if predicting:raise NotImplementedError('Original prediction damage accumulators required')
    cut=damage_cut_active(unit)
    if not cut and unit.get('hp_barrier',0) and not unit.get('hp_barriers'):
        raise NotImplementedError('Import the original barrier correction instance')
    if cut:
        correction=unit['damage_cut_correction']
        correction['remaining_count']=i32(correction['remaining_count']-1)
        unit['received_attack_count']=i32(unit.get('received_attack_count',0)+1)
        return
    unit['total_damage_taken']=i64(unit.get('total_damage_taken',0)+i32(hp_damage+barrier_damage))
    unit['received_attack_count']=i32(unit.get('received_attack_count',0)+1)
    barriers=unit.get('hp_barriers',[])
    if barriers:
        barriers[0]['current']=i32(barriers[0]['current']-barrier_damage)
        unit['hp_barrier']=barriers[0]['current']
    plus_hp(unit,i32(-hp_damage))
    if unit['hp']<=0:
        if is_unbreakable(unit) or can_escape:
            unit['hp']=0
        else:
            unit['existence']=2
            if 'chance_step' in unit:unit['chance_step']['existence']=2


def add_mp_change(unit,value):
    from .weapon_resource_damage import mp_change_enabled
    if mp_change_enabled(unit):unit['pending_mp_change']=i32(unit.get('pending_mp_change',0)+value)
