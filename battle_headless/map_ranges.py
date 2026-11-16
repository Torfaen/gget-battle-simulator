"""Original custom-range translation, self-centered and directional MAP ranges."""
import re
from .original import i32

# CoordParser's metadata literal; Int32.TryParse return flags are ignored by
# Coord(string), so malformed/overflowing matched components become zero.
_COORD_PATTERN=re.compile(r'\(([0-9-]*,[0-9-]*)\)')


def _unique(coords):
    return list(dict.fromkeys((i32(x),i32(z)) for x,z in coords))


def _try_int(value):
    try:result=int(value,10)
    except ValueError:return 0
    return result if -(2**31)<=result<2**31 else 0


def parse_custom_range(text):
    return _unique(tuple(_try_int(part) for part in match.group(1).split(','))
                   for match in _COORD_PATTERN.finditer(text))


def translate_range(offsets,origin):
    return _unique((i32(origin[0]+x),i32(origin[1]+z)) for x,z in offsets)


def around_self_range(offsets,occupied_coords):
    offsets=_unique(offsets);occupied_coords=_unique(occupied_coords)
    result=_unique(coord for origin in occupied_coords for coord in translate_range(offsets,origin))
    if (0,0) not in offsets:
        occupied=set(occupied_coords);result=[coord for coord in result if coord not in occupied]
    return result


def direction_coord(coord,direction):
    """DirectionExtension.ToDirectionCoord uses an axis swap for Right."""
    x,z=map(i32,coord);direction=i32(direction)
    if direction==1:return z,x
    if direction==3:return i32(-z),x
    if direction==4:return i32(-x),i32(-z)
    return x,z


def directional_range(offsets,occupied_coords,direction):
    occupied=set(_unique(occupied_coords))
    if not occupied:raise ValueError('Directional MAP requires an occupied footprint')
    xmin=min(x for x,z in occupied);xmax=max(x for x,z in occupied)
    zmin=min(z for x,z in occupied);zmax=max(z for x,z in occupied)
    result=[]
    for dx,dz in _unique(direction_coord(coord,direction) for coord in offsets):
        if i32(direction) in (2,4):
            edge=zmax if dz>0 else zmin
            candidates=((i32(x+dx),i32(edge+dz)) for x in range(xmin,xmax+1))
        else:
            edge=xmax if dx>0 else xmin
            candidates=((i32(edge+dx),i32(z+dz)) for z in range(zmin,zmax+1))
        result.extend(coord for coord in candidates if coord not in occupied)
    return _unique(result)


def fixed_position_range(offsets,shooting_coords,fixed_direction):
    shooting_coords=_unique(shooting_coords)
    origin=shooting_coords[0] if shooting_coords else (0,0)
    return translate_range((direction_coord(coord,fixed_direction) for coord in offsets),origin)


def effect_coords(env,attacker,weapon,impact_coord=None,direction=None):
    from .map_targets import filter_map_range
    direction=attacker.get('direction',2) if direction is None else direction
    origin=attacker['position'] if impact_coord is None else impact_coord
    offsets=parse_custom_range(weapon['effect_range'])
    kind=weapon['map_range_type']
    if kind in (1,3,4):
        footprint=env.battle_map.footprints[attacker['id']]
        occupied=footprint.ordered_coords(origin,direction)
        coords=around_self_range(offsets,occupied) if kind==1 else directional_range(offsets,occupied,direction)
    elif kind==2:
        if impact_coord is None:raise ValueError('ImpactRange requires a selected impact coordinate')
        coords=translate_range(offsets,impact_coord)
    elif kind==6:
        coords=fixed_position_range(offsets,parse_custom_range(weapon['shooting_range']),weapon['fixed_shooting_direction'])
    else:raise NotImplementedError('MAP range variant not ported')
    return filter_map_range(env,coords)


def self_centered_effect_coords(env,attacker,weapon):
    if weapon['map_range_type']!=1:raise NotImplementedError('MAP range variant not ported')
    from .map_targets import filter_map_range
    offsets=parse_custom_range(weapon['effect_range'])
    # AroundMyself passes the effect template unchanged; the actor's occupied
    # cells already carry its facing. Directional templates are another service.
    coords=around_self_range(offsets,env.battle_map.ordered_occupied(attacker))
    return filter_map_range(env,coords)


def prepare_self_centered_scope(env,attacker,weapon):
    from .map_targets import select_range_targets
    coords=self_centered_effect_coords(env,attacker,weapon)
    return dict(effect_coords=[list(coord) for coord in coords],
                targets=select_range_targets(env,attacker,coords,weapon['map_trait_category']))


def prepare_scope(env,attacker,weapon,impact_coord=None,direction=None):
    from .map_targets import select_range_targets,select_whole_map_targets
    if weapon['map_range_type']==5:
        return dict(effect_coords=None,targets=select_whole_map_targets(env,attacker,weapon['map_trait_category']))
    coords=effect_coords(env,attacker,weapon,impact_coord,direction)
    return dict(effect_coords=[list(coord) for coord in coords],
                targets=select_range_targets(env,attacker,coords,weapon['map_trait_category']))


def attach_map_ranges(env):
    env.map_range_profile='native-range-map-geometry-subset'
    env.map_weapon_scope=lambda attacker,weapon,impact_coord=None,direction=None:prepare_scope(env,attacker,weapon,impact_coord,direction)
