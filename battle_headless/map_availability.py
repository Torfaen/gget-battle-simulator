"""Original MAP availability order; moving destination resolution is external."""
from dataclasses import dataclass
from enum import IntEnum
from .original import i32
from .combat_primitives import valid_tension


class MapUnavailableReason(IntEnum):
    None_=0
    NotEnoughEn=1
    NotEnoughMp=2
    NotEnoughBullet=3
    NotEnoughTension=4
    MovementInability=5
    AttackInability=6
    UnavailableState=7
    HasNotCapability=8
    NoTarget=9
    ImpossibleArea=10
    NotEnoughHp=11


@dataclass(frozen=True)
class MapAvailabilityInputs:
    has_remaining_ammo:bool
    has_capability:bool
    attack_inability:bool
    can_move_after_any_attack:bool
    en:int
    en_cost:int
    mp:int
    mp_cost:int|None
    tension:int
    required_tension:int
    hp:int
    hp_cost:int|None
    has_moved:bool
    can_use_after_move:bool
    range_type:int
    movement_inability:bool=False


def before_check(state):
    r=MapUnavailableReason;result=[]
    if not state.has_remaining_ammo:result.append(r.NotEnoughBullet)
    if not state.has_capability:result.append(r.HasNotCapability)
    if state.attack_inability or state.can_move_after_any_attack:result.append(r.AttackInability)
    if i32(state.en)<i32(state.en_cost):result.append(r.NotEnoughEn)
    if state.mp_cost is not None and i32(state.mp)<i32(state.mp_cost):result.append(r.NotEnoughMp)
    if not valid_tension(state.tension,state.required_tension):result.append(r.NotEnoughTension)
    # A present zero is compared too, unlike normal weapon availability.
    if state.hp_cost is not None and i32(state.hp)<=i32(state.hp_cost):result.append(r.NotEnoughHp)
    if state.has_moved and not state.can_use_after_move:result.append(r.UnavailableState)
    if state.range_type==4 and state.movement_inability:result.append(r.MovementInability)
    return tuple(result)


def after_check(range_type,*,moving_immovable=None):
    if range_type!=4:return ()
    if moving_immovable is None:
        raise NotImplementedError('Original moving MAP destination/terrain immovability service required')
    return (MapUnavailableReason.ImpossibleArea,) if moving_immovable else ()


def availability(env,unit,weapon,records,*,has_moved,can_move_after_any_attack,standby_terrain_type=None):
    from .map_weapon_costs import costs
    from .map_weapon_runtime import unit_has_remaining_ammo
    from .map_context import target_terrain,TERRAIN_FIELDS
    from .weapon_corrections import runtime_weapon_records
    records=list(records)+runtime_weapon_records(unit)
    if standby_terrain_type is None:standby_terrain_type=target_terrain(env,unit)[0]
    field=TERRAIN_FIELDS.get(i32(standby_terrain_type))
    capability=bool(weapon['capability']['HasCapabilityIn'+field]) if field else False
    scalar=unit.get('scalar_corrections',{})
    from .tension import current_tension
    fee=costs(unit,weapon,records)
    state=MapAvailabilityInputs(
        has_remaining_ammo=unit_has_remaining_ammo(unit,weapon,weapon['map_level_stats']['ammo_capacity'],records),
        has_capability=capability,attack_inability=scalar.get('AttackInabilityCount',0)>0,
        can_move_after_any_attack=can_move_after_any_attack,en=unit['en'],en_cost=fee['en_cost'],
        mp=unit['mp'],mp_cost=fee['mp_cost'],tension=current_tension(unit,env.rules.kernel),
        required_tension=weapon['required_tension'],hp=unit['hp'],hp_cost=fee['hp_cost'],has_moved=has_moved,
        can_use_after_move=weapon['can_use_after_move'],range_type=weapon['map_range_type'],
        movement_inability=scalar.get('MovementInabilityCount',0)>0)
    reasons=before_check(state)
    return dict(available_before_check=not reasons,reasons=list(map(int,reasons)),costs=fee,
                after_check_required=weapon['map_range_type']==4,complete_action_eligibility=False)
