"""Original CalculateBattleResult entry, before hit/damage calculation.

This is a calculator building block, not a complete attack/prediction method.
The services provide the current repository, weapon and condition getters.
"""
from dataclasses import dataclass
from .original import i32


@dataclass
class BattleCalculationEntry:
    map_root: object
    damaged_unit: object
    counter_type: int
    target_is_broken: bool
    weapon: object
    applied_passive_ids: object
    refresh_units: list
    inability_result: object = None


def prepare_battle_calculation(services,attacker,attack_coord,weapon_id,main_target,
                              target_coord,counter_type,guard,participation,*,prediction):
    """Preserve original entry order, including its two GetWeapon calls.

    An inability result is already saved by the original early-return branch.
    Neither branch here disables weapon traits or clears active participation.
    The normal branch must continue through the original calculator body.
    """
    s=services;units=s.refresh_units
    units.clear();units.append(attacker);units.append(main_target)
    map_root=s.map_root()
    target=main_target;effective_counter=i32(counter_type)
    if guard is not None:
        units.append(guard);target=guard;effective_counter=3
    broken=False if s.can_escape(target) else s.is_broken(target)
    weapon=s.get_weapon(attacker,weapon_id)
    if participation is not None:
        attacker_places=s.expand_unit_coord(attacker,attack_coord,0)
        # Both distance and later terrain lookup use the protected main target.
        target_places=s.expand_unit_coord(main_target,target_coord,0)
        identity=s.unit_id(attacker)
        distance=s.min_distance(attacker_places,target_places)
        s.set_active_attacker(participation,identity,weapon,distance)
    identity=s.unit_id(attacker)
    traits=s.weapon_traits(weapon)
    applied=s.enable_weapon_passives(identity,traits,participation,prediction)
    s.execute_always(units,participation)
    # Always may change the entity's effective weapon and inability state.
    weapon=s.get_weapon(attacker,weapon_id)
    action_inability=s.is_action_inability(attacker)
    attack_inability=s.is_attack_inability(attacker)
    result=None
    if action_inability or attack_inability:
        result=s.inability_result(weapon,action_inability,attack_inability)
        s.save_result(s.unit_id(attacker),result)
    return BattleCalculationEntry(map_root,target,effective_counter,broken,weapon,applied,units,result)
