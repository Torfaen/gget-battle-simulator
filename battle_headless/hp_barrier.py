"""HP barrier splitting for explicitly imported, normalized barrier state.

Trait creation, replacement and expired-barrier removal remain separate lifecycle
services. This module never grants a barrier from a guessed ability definition.
"""
from .original import i32
from .weapon_corrections import matches_weapon

def barrier_damage(damage, durability, weapon_matches=True):
    # TraitCorrectionHpDamageBarrier.CalcDamage 0x5fa3920.
    return min(damage,durability) if durability>=1 and weapon_matches else 0

def split_damage(target, weapon, total):
    condition=target.get('hp_barrier_weapon_condition')
    if condition is not None and (set(condition)-{'WeaponAttributeTypes','AttackAttributeTypes'}):
        raise NotImplementedError('Unported barrier weapon condition')
    absorbed=barrier_damage(total,target.get('hp_barrier',0),matches_weapon(condition,weapon))
    return i32(total-absorbed),absorbed

def apply_barrier_damage(target, result):
    amount=result.get('hp_barrier_damage',0)
    if amount:
        # Original TakeDamage subtracts as Int32 without clamping.
        target['hp_barrier']=i32(target.get('hp_barrier',0)-amount)
        if target.get('hp_barriers'):
            target['hp_barriers'][0]['current']=target['hp_barrier']
    result['applied_barrier_damage']=amount


def _publish_first(unit):
    rows=unit.get('hp_barriers',[])
    first=rows[0] if rows else None
    unit['hp_barrier']=first['current'] if first else 0
    unit['max_hp_barrier']=first['maximum'] if first else 0
    unit['hp_barrier_weapon_condition']=first['weapon_condition'] if first else None

def register_barrier(unit, instance_id, source_max_hp, rate, weapon_condition=None):
    """Register one native trait-instance correction; repeated Execute is a no-op.

    source_max_hp is supplied by the original trait's source-unit delegate. It
    must not be replaced with recipient HP for effects cast by another unit.
    """
    from copy import deepcopy
    from .combat_primitives import resource_percent
    rows=unit.get('hp_barriers',[])
    if any(row['instance_id']==instance_id for row in rows):
        return False
    if rows==[] and unit.get('hp_barrier',0):
        raise ValueError('Import existing barrier instances before registering new ones')
    if weapon_condition is not None and set(weapon_condition)-{'WeaponAttributeTypes','AttackAttributeTypes'}:
        raise NotImplementedError('Unported barrier weapon condition')
    maximum=resource_percent(source_max_hp,rate)
    rows.append(dict(instance_id=instance_id,maximum=maximum,current=maximum,
                     weapon_condition=deepcopy(weapon_condition)))
    unit['hp_barriers']=rows
    _publish_first(unit)
    return True

def restore_barrier(unit, current):
    # RestoreHpDamageBarrier restores only the first barrier, without clamping.
    if current is not None and unit.get('hp_barriers'):
        unit['hp_barriers'][0]['current']=current
        _publish_first(unit)

def remove_broken_barrier(unit, instance_id):
    """CanRemove checks both first barrier's trait identity and CurrentValue<1."""
    rows=unit.get('hp_barriers',[])
    if not rows or rows[0]['instance_id']!=instance_id or rows[0]['current']>=1:
        return False
    rows.pop(0)
    _publish_first(unit)
    return True
