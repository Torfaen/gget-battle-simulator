"""Original numeric trait-condition predicates (MasterTraitCondition, client 2.7.0).

These predicates do not implement grouped target selection or map participants.
None is the original absent nullable threshold; explicit zero remains meaningful.
"""
from .original import mul, div


def hp_threshold(current, maximum, *, lte=None, gte=None, damaged=False):
    if damaged and current>=maximum:return False
    if lte is None and gte is None:return True
    if maximum==0:return False
    ratio=div(mul(current,100),maximum)
    return ratio<=(2147483647 if lte is None else lte) and ratio>=(0 if gte is None else gte)


def en_threshold(current, maximum, *, rate_lte=None, rate_gte=None, value_lte=None, value_gte=None):
    if all(v is None for v in (rate_lte,rate_gte,value_lte,value_gte)):return True
    if maximum==0:return False
    ratio=div(mul(current,100),maximum)
    return (ratio<=(2147483647 if rate_lte is None else rate_lte)
        and ratio>=(0 if rate_gte is None else rate_gte)
        and current<=(2147483647 if value_lte is None else value_lte)
        and current>=(0 if value_gte is None else value_gte))


def turn_threshold(current, required=None):
    return required is None or required<=current


def required_flag(current, required=False):
    return not required or bool(current)


def attack_distance(current, *, gte=None, lte=None):
    if gte is not None and (current is None or current<gte):return False
    if lte is not None and (current is None or current>lte):return False
    return True


def weapon_base_range(current, *, min_gte=None,min_lte=None,max_gte=None,max_lte=None):
    if all(v is None for v in (min_gte,min_lte,max_gte,max_lte)):return True
    if current is None:return False
    minimum,maximum=current
    return (attack_distance(minimum,gte=min_gte,lte=min_lte)
            and attack_distance(maximum,gte=max_gte,lte=max_lte))
