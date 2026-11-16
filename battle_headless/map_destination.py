"""Original moving MAP destination for ordered, non-overflowing range keys."""
from .original import i32
from .map_ranges import direction_coord,parse_custom_range


def destination_coord(origin,occupied_at_zero,shooting_offsets,direction):
    if direction not in (1,2,3,4):raise ValueError('Moving MAP requires a cardinal direction')
    occupied=list(occupied_at_zero)
    if not occupied:raise ValueError('Original occupied place set required')
    shooting=list(dict.fromkeys(direction_coord(coord,direction) for coord in shooting_offsets))
    if not shooting:raise ValueError('Original moving MAP shooting range cannot be empty')
    axis=0 if direction in (1,3) else 1
    keys=[coord[axis] for coord in shooting]
    # Original comparison delegates subtract Int32 keys. Their ordering is not
    # equivalent to numeric extrema when subtraction overflows; that exceptional
    # value-array sorting path needs its own port, rather than guessing a key.
    if max(keys)-min(keys)>2147483647:
        raise NotImplementedError('Original overflowing Coord comparison sort required')
    low=min(coord[axis] for coord in occupied);high=max(coord[axis] for coord in occupied)
    result=list(map(i32,origin))
    if direction in (1,2):result[axis]=i32(result[axis]+high+max(keys)+low+1)
    else:result[axis]=i32(result[axis]+low+min(keys)-high-1)
    return tuple(result)


def move_destination(env,unit,weapon,*,origin):
    if weapon['map_range_type']!=4:raise ValueError('MovingAttack MAP weapon required')
    direction=unit['direction']
    occupied=env.battle_map.footprints[unit['id']].ordered_coords((0,0),direction)
    return destination_coord(origin,occupied,parse_custom_range(weapon['shooting_range']),direction)
