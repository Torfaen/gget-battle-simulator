"""Native target defense/mobility reduction is a subtraction from buff rate."""
from .original import i32

def reduced_target_stat(unit, stat, reduction):
    if reduction==0:
        return unit[stat]
    from .owner_passives import unit_stat
    c=unit.get('scalar_corrections',{})
    base=unit.get('_passive_initial_state',unit)[stat]
    stem=stat.title()
    return unit_stat(base,i32(c.get(stem+'Rate',0)-reduction),c.get(stem+'Value',0))
