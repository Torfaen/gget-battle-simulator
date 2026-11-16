"""Original five-place formation geometry on the bounded game map.

MapPlaceDecider 0x6223b2c..0x622488c; MasterUnitSize.GetOriginCoord
0x61546ec. Creation order, PrepareSortie and trait activation are separate.
"""
from .original import i32


def formation_left_bottom(criterion, direction, number, is_single_square):
    """PlaceByNumber; the original boolean comes from Width == 1.

    The native implementation rotates all cells of a 1x1 or 2x2 Area around
    criterion, then returns its lower-left bound. Quarter-turn integer math
    agrees on the bounded grid; large coordinates need the native float path.
    """
    x,z=criterion
    if any(type(v) is not int or abs(v)>128 for v in (x,z)):
        raise NotImplementedError('Deployment coordinate outside verified bounded grid')
    if direction not in (0,1,2,3,4):raise ValueError('Unknown deployment direction')
    if number not in (1,2,3,4,5):raise ValueError('Formation number must be 1..5')
    if type(is_single_square) is not bool:raise TypeError('Explicit Width == 1 flag required')
    if number==1 and is_single_square:return x,z
    size=1 if is_single_square else 2
    dx,dz={1:(0,-1),2:(-2,-1),3:(2,-1),
           4:(-1,-2 if is_single_square else -3),
           5:(1,-2 if is_single_square else -3)}[number]
    cells=[]
    for a in range(dx,dx+size):
        for b in range(dz,dz+size):
            if direction==1:rx,rz=b,-a
            elif direction==3:rx,rz=-b,a
            elif direction==4:rx,rz=-a,-b
            else:rx,rz=a,b
            cells.append((x+rx,z+rz))
    return min(a for a,b in cells),min(b for a,b in cells)


def unit_origin(left_bottom,footprint,direction):
    """Native GetOriginCoord adds directional strip sizes, with Int32 wrap."""
    x,z=map(i32,left_bottom)
    if direction==1:dx,dz=footprint.rear,footprint.right
    elif direction==2:dx,dz=footprint.left,footprint.rear
    elif direction==3:dx,dz=footprint.front,footprint.left
    elif direction==4:dx,dz=footprint.right,footprint.front
    elif direction==0:dx,dz=0,0
    else:raise ValueError('Unknown deployment direction')
    return i32(x+dx),i32(z+dz)


def formation_positions(criterion,direction,footprints):
    """Explicit converted list order, including null slots, maximum five.

    DefaultUnitInitializer passes the converted formation list to Deploy.
    Non-null converted units already have increasing UnitStepOrder. Its sort
    comparator returns zero for a null operand, retaining these empty slots
    in the five-element insertion-sort path. Missing slots are not compacted.
    """
    footprints=list(footprints)
    if len(footprints)>5:raise ValueError('Original formation deploys at most five places')
    return [None if footprint is None else unit_origin(
        formation_left_bottom(criterion,direction,index+1,footprint.width==1),footprint,direction)
        for index,footprint in enumerate(footprints)]
