"""BattleParticipationStatus construction over canonical repository units.

Selection eligibility remains an explicit service input. This module does not
infer step selections or the caller's strike/update lifecycle.
"""
from dataclasses import dataclass
from .original import i32


@dataclass(frozen=True)
class CounterSelectionValue:
    type: int = 0
    weapon_id: object = None


def unit_id(unit):
    return None if unit is None else unit['id']


class BattleParticipationState:
    """Original constructor, cached lists, mutations and inactive clone.

    Support lists are retained; the three caches are independent snapshots.
    Units themselves are always canonical references, including in a clone.
    """
    def __init__(self,main_attacker=None,main_target=None,support_attackers=None,
                 support_defender=None,counter_supporters=None,
                 attack_side_support_defender=None,counter_selection=None):
        self.main_attacker_unit=main_attacker
        self.main_target_unit=main_target
        self.support_defender_unit=support_defender
        self.attack_side_support_defender_unit=attack_side_support_defender
        self.support_attack_units=[] if support_attackers is None else support_attackers
        self.counter_support_units=[] if counter_supporters is None else counter_supporters
        if counter_selection is None:counter_selection=CounterSelectionValue()
        elif isinstance(counter_selection,int):counter_selection=CounterSelectionValue(i32(counter_selection))
        self.main_target_counter_selection=CounterSelectionValue(i32(counter_selection.type),counter_selection.weapon_id)
        self.participants=[u for u in (main_attacker,main_target,support_defender,
            attack_side_support_defender) if u is not None]
        self.participants.extend(self.support_attack_units)
        self.participants.extend(self.counter_support_units)
        self.attack_side_units=[u for u in (main_attacker,attack_side_support_defender) if u is not None]
        self.attack_side_units.extend(self.support_attack_units)
        self.counter_side_units=[u for u in (main_target,support_defender) if u is not None]
        self.counter_side_units.extend(self.counter_support_units)
        self.clear_active_attacker()
        self.attack_side_damaged=unit_id(attack_side_support_defender if
            attack_side_support_defender is not None else main_attacker)
        self.counter_side_damaged=unit_id(support_defender if support_defender is not None else main_target)

    @property
    def main_attacker(self):return unit_id(self.main_attacker_unit)
    @property
    def main_target(self):return unit_id(self.main_target_unit)
    @property
    def support_defender(self):return unit_id(self.support_defender_unit)
    @property
    def attack_side_support_defender(self):return unit_id(self.attack_side_support_defender_unit)
    @property
    def support_attackers(self):return tuple(unit_id(u) for u in self.support_attack_units)
    @property
    def counter_supporters(self):return tuple(unit_id(u) for u in self.counter_support_units)
    @property
    def counter_selection(self):return self.main_target_counter_selection.type

    def set_active_attacker(self,attacker_id,weapon,attack_distance):
        self.active_attacker=attacker_id
        self.active_attacker_weapon=weapon
        self.attack_distance=i32(attack_distance)

    def update_active_damaged_unit_id(self,attack_side_id,counter_side_id):
        self.attack_side_damaged=attack_side_id
        self.counter_side_damaged=counter_side_id

    def clear_active_attacker(self):
        self.active_attacker=None
        self.active_attacker_weapon=None
        self.attack_distance=None

    def clone_without_active_attacker(self):
        clone=BattleParticipationState(self.main_attacker_unit,self.main_target_unit,
            list(self.support_attack_units),self.support_defender_unit,
            list(self.counter_support_units),self.attack_side_support_defender_unit,self.main_target_counter_selection)
        clone.update_active_damaged_unit_id(self.attack_side_damaged,self.counter_side_damaged)
        return clone

    def find_unit(self,identity):
        # FindUnit reads the roles/live support lists, not the Participants cache.
        for unit in (self.main_attacker_unit,self.main_target_unit,self.support_defender_unit,
                     self.attack_side_support_defender_unit):
            if unit_id(unit)==identity:return unit
        for units in (self.support_attack_units,self.counter_support_units):
            for unit in units:
                if unit is None:raise ValueError('Original FindUnit dereferences a null support member')
                if unit['id']==identity:return unit
        return None

    def contains(self,identity):return self.find_unit(identity) is not None


def create_participation(services,main_attacker,main_target,step):
    """Factory.Create, 0x5eecdf8; EnabledOnly and repository are services.

    A selected record is a mapping with unit_id. Missing repository units are
    retained, as in the original; they are not silently removed or replaced.
    """
    if step is None:raise ValueError('Original participation factory requires a step')
    s=services
    defender=None
    if s.is_support_defend_enabled(step):
        defender=s.find_unit(s.enabled_support_defender(step)['unit_id'])
    attack_defender=None
    if s.is_attack_side_support_defend_enabled(step):
        attack_defender=s.find_unit(s.enabled_attack_side_support_defender(step)['unit_id'])
    counters=[s.find_unit(record['unit_id']) for record in s.enabled_counter_supporters(step)]
    attackers=[s.find_unit(record['unit_id']) for record in s.enabled_support_attackers(step)]
    selection=s.counter_selection(step)
    return BattleParticipationState(main_attacker,main_target,attackers,defender,
        counters,attack_defender,selection)
