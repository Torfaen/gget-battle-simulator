"""Original persistent and one-time chance-step/support trait corrections."""
from .original import i32
from .mechanics import short
from .trait_consumption import remaining_value

COUNT_FIELDS={19:'ChanceStepMaxCount',51:'SupportAttackAndCounterLimit',52:'SupportDefenseLimit'}
ONCE_COUNTERS={(19,15):('chance_step','once'),(19,23):('chance_step','guaranteed_once'),
               (51,16):('support_budget','attack_once'),(52,17):('support_budget','defense_once')}


def count_correction(trait):
    kind=trait['trait_type']
    if kind not in COUNT_FIELDS:raise ValueError('Chance-step or support trait required')
    return {} if (kind,trait['action_timing']) in ONCE_COUNTERS else {COUNT_FIELDS[kind]:i32(trait['trait_value'])}


def count_side_effect(trait,unit,*,removing=False):
    """OnExecute adds remaining uses; OnRemove subtracts remaining uses.

    All four mutable unit counters store signed Int16 values. Ordinary
    timings only add/remove the persistent scalar correction, not a counter.
    """
    if trait['trait_type'] not in COUNT_FIELDS:raise ValueError('Chance-step or support trait required')
    location=ONCE_COUNTERS.get((trait['trait_type'],trait['action_timing']))
    if location is None:return
    container,field=location
    delta=remaining_value(trait)*(-1 if removing else 1)
    unit[container][field]=short(i32(unit[container][field]+delta))
