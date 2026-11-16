"""Original MaxHp/MaxEn/InitialMp trait first-execution side effects.

The caller installs the scalar correction before invoking OnExecute. Repeated
Execute calls use the existing identity and must not run this callback again.
"""
from .original import i32
from .combat_primitives import resource_percent,initial_mp


RESOURCE_FIELDS={31:'MaxHpRate',32:'MaxHpValue',33:'MaxEnRate',34:'MaxEnValue',46:'InitialMp'}


def resource_correction(trait,boost_count=None):
    kind=trait['trait_type']
    if kind not in RESOURCE_FIELDS:raise ValueError('Resource trait required')
    value=i32(trait['trait_value'])
    # InitialMpChangeValueTrait ignores boostCount in both overrides.
    if kind!=46:value=i32(value*(1 if boost_count is None else i32(boost_count)))
    return {RESOURCE_FIELDS[kind]:value}


def resource_on_execute(trait,unit,boost_count=None,*,predicting=False):
    """Resolved original OnExecute, after registration/maximum recomputation.

    HP growth calls AdjustHpByMaxHpIncrease rather than RecoverHpByValue, so it
    does not contribute to the battle healing accumulator. HP/EN mutators skip
    writes in prediction, whereas InitialMp calls SetMp directly.
    """
    kind=trait['trait_type'];value=next(iter(resource_correction(trait,boost_count).values()))
    if kind==46:
        unit['mp']=initial_mp(i32(unit['mp']+value))
    elif value>0 and not predicting:
        if kind in (31,32):
            amount=resource_percent(unit['max_hp'],value) if kind==31 else value
            unit['hp']=min(unit['max_hp'],i32(min(unit['hp'],unit['max_hp'])+amount))
        else:
            amount=resource_percent(unit['max_en'],value) if kind==33 else value
            unit['en']=max(0,min(unit['max_en'],i32(min(unit['en'],unit['max_en'])+amount)))


def cannot_remove_resource(trait,unit):
    """All five original subclasses return false from CanRemove."""
    return False
