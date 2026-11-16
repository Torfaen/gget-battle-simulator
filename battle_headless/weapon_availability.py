"""Original CheckWeaponAvailability decision order (2.7.0, RVA 0x61feed4).

Inputs are evaluated original properties. Geometry, capability and weapon cost
services must supply these values; this module does not approximate them.
"""
from dataclasses import dataclass
from enum import IntEnum
from .original import i32
from .combat_primitives import valid_tension


class UnavailableReason(IntEnum):
    OutOfRange=1
    NotEnoughEn=2
    NotEnoughMp=3
    NotEnoughTension=4
    HasNotCapability=5
    ActionInability=6
    DamageEfficiencyIsZero=7
    AttackInability=8
    NotEnoughHp=9


@dataclass(frozen=True)
class WeaponAvailabilityInputs:
    remaining_hp: int
    en: int
    en_consumption: int
    mp: int
    mp_consumption: int | None
    tension: int
    required_tension: int
    hp_consumption: int
    has_capability: bool
    action_inability: bool=False
    attack_inability: bool=False
    has_target: bool=False
    has_target_coord: bool=False
    within_range: bool=True
    damage_effective: bool=True


def unavailable_reasons(state):
    """Return ordered original reason codes, retaining multiple failures.

    Action/attack inability returns immediately. Other checks accumulate. Target
    range AND target damage capability run only when target and coord both exist.
    Nonzero HP cost must leave strictly positive predicted remaining HP.
    """
    r=UnavailableReason
    if state.action_inability:return (r.ActionInability,)
    if state.attack_inability:return (r.AttackInability,)
    reasons=[]
    if not state.has_capability:reasons.append(r.HasNotCapability)
    if i32(state.en)<i32(state.en_consumption):reasons.append(r.NotEnoughEn)
    if state.mp_consumption is not None and i32(state.mp)<i32(state.mp_consumption):reasons.append(r.NotEnoughMp)
    if not valid_tension(state.tension,state.required_tension):reasons.append(r.NotEnoughTension)
    cost=i32(state.hp_consumption)
    if cost!=0 and cost>=i32(state.remaining_hp):reasons.append(r.NotEnoughHp)
    if state.has_target and state.has_target_coord:
        if not state.within_range:reasons.append(r.OutOfRange)
        if not state.damage_effective:reasons.append(r.DamageEfficiencyIsZero)
    return tuple(reasons)
