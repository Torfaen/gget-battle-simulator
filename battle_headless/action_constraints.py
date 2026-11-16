"""Native inability counters; geometry remains independent of command gates."""
from .original import i32

INABILITY_FIELDS={68:'MovementInabilityCount',69:'ActionInabilityCount',71:'AttackInabilityCount'}


def inability_active(count):
    return i32(count)>0


def movement_blocked(unit):
    corrections=unit.get('scalar_corrections',{})
    return inability_active(corrections.get('MovementInabilityCount',0))
