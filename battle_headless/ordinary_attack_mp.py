"""Original ordinary-attack MP event helper; invocation order is external.

AttackUseCase queues these changes during _ChangeMpAsync, after the strikes.
This module does not schedule that phase, defeat rewards, or MP reflection.
"""
from dataclasses import dataclass
from .original import i32
from .trait_factory import i64
from .map_action_mp import queue_battle_action_mp


@dataclass(frozen=True)
class AttackMpChangeValues:
    hit: int
    damaged: int
    evaded: int
    miss: int
    main_attack_kill: int
    any_attack_kill: int
    ally_killed: int
    support_defense: int

    @classmethod
    def from_coefficients(cls,coefficients):
        # Original _ChangeMpAsync constructs its value object in this order.
        return cls(*(i32(coefficients[kind]) for kind in (3,6,4,5,1,2,7,8)))

    @classmethod
    def from_master(cls,master):
        return cls.from_coefficients({row['IngameCoefficientConfigTypeIndex']:row['Value']
                                     for row in master.tables['m_ingame_coefficient_config']})


def queue_attack_mp(values,actor,protected,guard,*,is_hit,hp_total_damage):
    """_AddMpOnAttack: signed Int64 damage gate and original recipient/order.

    A hit always awards the actor; positive total HP damage awards the actual
    recipient. A selected guard earns support-defense MP even at zero damage.
    A miss applies attack-failure handling, then awards the protected target.
    The original helper has no existence/HP gate.
    """
    events=[]
    def queue(unit,kind,failure=False):
        value=i32(getattr(values,kind))
        delta=queue_battle_action_mp(unit,value,attack_failure=failure)
        events.append(dict(unit_id=unit['id'],kind=kind,value=value,attack_failure=failure,queued_mp=delta))
    if is_hit:
        queue(actor,'hit')
        if i64(hp_total_damage)>0:queue(guard if guard is not None else protected,'damaged')
        if guard is not None:queue(guard,'support_defense')
    else:
        queue(actor,'miss',True);queue(protected,'evaded')
    return events
