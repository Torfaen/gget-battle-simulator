"""Original weapon EN/MP attack values and immediate/deferred mutations."""
from .original import i32, div, round_up

def en_attack_value(traits, target_max_en):
    flat=rate_product=0
    exists=False
    for trait in traits:
        kind=trait['WeaponTraitTypeIndex']
        if kind==8:
            exists=True;flat=i32(flat+trait['TargetValue'])
        elif kind==14:
            exists=True;rate_product=i32(rate_product+i32(trait['TargetValue']*target_max_en))
    return i32(flat+round_up(div(rate_product,100))) if exists else None

def mp_attack_value(traits):
    result=None
    for trait in traits:
        if trait['WeaponTraitTypeIndex']==9:
            result=i32((result or 0)+trait['TargetValue'])
    return result

def mp_change_enabled(unit):
    c=unit.get('scalar_corrections',{})
    return c.get('AntiMpChangeCount',0)<=0 and c.get('MpLimitBreakCount',0)<=0

def plus_en(unit, delta):
    before=unit['en']
    unit['en']=max(0,min(unit['max_en'],i32(min(before,unit['max_en'])+delta)))
    return unit['en']-before

def apply_resource_damage(target, result):
    # AttackService applies negative EN recovery and queues direct MP change;
    # this MP path does not apply the battle-action fluctuation multiplier.
    # AttackService removes a broken barrier after HP application, before EN/MP.
    if result.get('hit') and not result.get('parry') and target.get('hp_barriers'):
        from .hp_barrier import remove_broken_barrier
        first=target['hp_barriers'][0]['instance_id']
        if remove_broken_barrier(target,first):result['removed_barrier_instance']=first
    en=result.get('en_damage')
    if en is not None:
        result['applied_en_damage']=-plus_en(target,i32(-en))
    mp=result.get('mp_damage')
    if mp is not None:
        delta=i32(-mp) if mp_change_enabled(target) else 0
        target['pending_mp_change']=i32(target.get('pending_mp_change',0)+delta)
        result['queued_mp_damage']=i32(-delta)
