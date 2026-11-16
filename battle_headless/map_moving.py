"""Original moving MAP wall/occupancy decision with explicit map services.

Destination calculation and terrain wall resolution remain separate original
services; the experimental movement BFS is not used here.
"""
from .map_availability import MapUnavailableReason


def can_standby_places(unit_id,places,standby_terrain_type,wall_traits,*,contain_walls,find_unit_ids):
    # MapRootEntity.CanStandbyPlacesForUnit checks walls before occupants.
    if contain_walls(places,standby_terrain_type,wall_traits):return False
    occupants=find_unit_ids(places)
    if occupants is None:return True
    occupants=set(occupants)
    return not occupants or occupants=={unit_id}


def is_immovable(destination_places,shooting_coords,standby_terrain_type,wall_traits,*,can_standby,contain_walls):
    # _IsImmovable returns immediately on a failed destination check. Only the
    # shooting range is wrapped in a new MapPlaceSet, preserving first order.
    if not can_standby(destination_places):return True
    shooting_places=list(dict.fromkeys(tuple(coord) for coord in shooting_coords))
    return bool(contain_walls(shooting_places,standby_terrain_type,wall_traits))


def moving_after_check(unit_id,destination_places,shooting_coords,standby_terrain_type,wall_traits,*,contain_walls,find_unit_ids):
    def standby(places):
        return can_standby_places(unit_id,places,standby_terrain_type,wall_traits,
            contain_walls=contain_walls,find_unit_ids=find_unit_ids)
    blocked=is_immovable(destination_places,shooting_coords,standby_terrain_type,wall_traits,
        can_standby=standby,contain_walls=contain_walls)
    return (MapUnavailableReason.ImpossibleArea,) if blocked else ()
