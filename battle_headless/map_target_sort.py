"""Original coordinate comparators used by MAP target DTO sorting.

This is the explicit Coord/Coord/origin constructor path. Area/footprint
primary-coordinate selection is a different SpiralSort overload.
"""
import ctypes
from .original import i32,f32,sub,add,_libm
from .native_priority_sort import priority_sort

_atan2f=_libm.atan2f
_atan2f.argtypes=[ctypes.c_float,ctypes.c_float]
_atan2f.restype=ctypes.c_float


def delta(coord,origin):return tuple(i32(a-b) for a,b in zip(coord,origin))


def spiral_distance(point):
    # Native CNEG retains INT_MIN; the max uses unsigned comparison.
    values=[i32(-v if v<0 else v)&0xffffffff for v in point]
    return i32(max(values))


def spiral_angle(point):
    x,z=point
    angle=sub(f32(1.5707963705062866),float(_atan2f(f32(z),f32(x))))
    return add(angle,f32(6.2831854820251465)) if angle<0 else angle


def spiral_compare(a,b,origin):
    a,b=delta(a,origin),delta(b,origin)
    da,db=spiral_distance(a),spiral_distance(b)
    if da!=db:return (da>db)-(da<db)
    aa,ab=spiral_angle(a),spiral_angle(b)
    return (aa>ab)-(aa<ab)


def directed_compare(a,b,origin,direction):
    a,b=delta(a,origin),delta(b,origin)
    # Compare signed values directly, not negated Int32 sort keys: negating
    # INT_MIN would invert native CompareTo ordering.
    axes={1:((0,1),(1,-1)),2:((1,1),(0,1)),
          3:((0,-1),(1,1)),4:((1,-1),(0,-1))}
    for axis,sign in axes.get(direction,()):
        comparison=(a[axis]>b[axis])-(a[axis]<b[axis])
        if comparison:return sign*comparison
    return 0


def sort_results(results,target_sort_type,origin,*,direction=2):
    rows=list(results)
    if target_sort_type not in (1,2) or not rows:return rows
    if any('target_coord' not in row for row in rows):
        raise NotImplementedError('Original target DTO coordinates required for MAP sorting')
    compare=(lambda a,b:spiral_compare(a['target_coord'],b['target_coord'],origin)) if target_sort_type==1 else (
        lambda a,b:directed_compare(a['target_coord'],b['target_coord'],origin,direction))
    return priority_sort(rows,compare=compare)


def service_sort_parameters(weapon,impact_coords,*,step_free_move_coord=None,direction):
    """MapWeaponServiceBase.Execute policy table 0x27a23f0 and origin branch."""
    kind=weapon['map_range_type']
    mode={1:1,2:1,3:2,4:2,5:1,6:2}.get(kind,0)
    if kind==2:
        if 'impact_point_max_select_count' not in weapon:
            raise NotImplementedError('Original master MAP impact-point selection count required')
        multi=weapon['impact_point_max_select_count']>=2
    else:multi=False
    if multi:
        if step_free_move_coord is None:
            raise NotImplementedError('Multi-lock MAP sorting requires original step FreeMoveUnitCoord')
        origin=tuple(step_free_move_coord)
    else:
        origin=next(iter(impact_coords),(0,0))
    return dict(target_sort_type=mode,origin=list(origin),direction=direction)


def service_sort_results(results,parameters):
    # Execute constructs the returned sorted-list wrapper, then the view
    # service constructs another wrapper over the SAME underlying List.
    # Both constructors sort it in place. Equal-coordinate ties can move twice.
    rows=list(results)
    for _ in range(2):
        rows=sort_results(rows,parameters['target_sort_type'],parameters['origin'],direction=parameters['direction'])
    return rows
