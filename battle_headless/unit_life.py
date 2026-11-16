"""Separate native zero-HP survival from defeat in ordinary damage paths."""
def is_alive(unit):
    return unit['hp']>0 or unit.get('zero_hp_survival',False)

def apply_hp_damage(target,result,damage):
    requested=result.get('hp_damage',damage)
    applied=min(max(0,target['hp']),requested)
    target['hp']-=applied
    # Native Damage preserves 0 HP without Crush when IsUnbreakable is true.
    # Miss/parry never enters this Damage branch and cannot revoke existence.
    if result.get('hit',True) and not result.get('parry',False) and target['hp']<=0:
        survives=target.get('scalar_corrections',{}).get('UnbreakableCount',0)>0
        target['zero_hp_survival']=survives
        if survives:result['unbreakable_survival']=True
    elif target['hp']>0:
        target.pop('zero_hp_survival',None)
    return applied
