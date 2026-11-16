"""WeaponTraitList drain arithmetic from the version-local ARM64 client."""
from .original import i32, div, round_up

def drain_value(damage, maximum, rate, limit):
    # Multiply as signed Int32 BEFORE Min, float32 division and ceiling.
    return round_up(div(min(i32(rate * damage), i32(limit * maximum)), 100))

def first_drain(traits, kind, damage, maximum):
    # Native list scan returns the first matching drain class, without summing.
    for trait in traits:
        if trait['WeaponTraitTypeIndex'] == kind:
            return drain_value(damage, maximum, trait['TargetValue'], trait['TargetLimit'])
    return None

def apply_hp_drain(unit, result):
    if result.get('drained_hp') is None:
        return
    before=unit['hp']
    unit['hp']=min(unit['max_hp'],i32(min(unit['hp'],unit['max_hp'])+result['drained_hp']))
    result['applied_hp_drain']=unit['hp']-before

def apply_en_drain(unit, result):
    if result.get('drained_en') is not None:
        from .weapon_resource_damage import plus_en
        result['applied_en_drain']=plus_en(unit,result['drained_en'])
