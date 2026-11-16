"""Original rectangular area and normal AroundRange geometry, client 2.7.0.

Direction-dependent unit expansion and map range variants are separate services.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Area:
    x: int
    z: int
    width: int=1
    height: int=1

    def __post_init__(self):
        if self.width<1 or self.height<1:raise ValueError('Positive occupied dimensions required')

    @property
    def left(self):return self.x
    @property
    def right(self):return self.x+self.width-1
    @property
    def bottom(self):return self.z
    @property
    def top(self):return self.z+self.height-1

    def coords(self):
        return {(x,z) for x in range(self.left,self.right+1) for z in range(self.bottom,self.top+1)}

    def contains(self,coord):
        x,z=coord
        return self.left<=x<=self.right and self.bottom<=z<=self.top

    @classmethod
    def from_coords(cls,coords):
        coords=set(coords)
        if not coords:raise ValueError('Occupied coordinates cannot be empty')
        left=min(x for x,z in coords);right=max(x for x,z in coords)
        bottom=min(z for x,z in coords);top=max(z for x,z in coords)
        return cls(left,bottom,right-left+1,top-bottom+1)


@dataclass(frozen=True)
class ExcludeArea:
    identifier: int
    area: Area
    opened: bool=False


@dataclass(frozen=True)
class MapGeometry:
    bounds: Area
    exclusions: tuple=()

    @classmethod
    def from_dimensions(cls,width,height,*,initial_area=None,exclusions=()):
        # SerializableMapEntity.ConvertWithMaster 0x5f42fc0: only create a
        # zero-origin master-size area when no serialized initial area exists.
        return cls(initial_area if initial_area is not None else Area(0,0,width,height),tuple(exclusions))

    def exists(self,coord):
        """MapEntity.ExistCoord 0x5f33148: closed excludes, then map bounds."""
        for exclusion in self.exclusions:
            if not exclusion.opened and exclusion.area.contains(coord):return False
        return self.bounds.contains(coord)


def around_range(area,minimum,maximum,include_diagonals=True):
    """AroundRange.CalculateRangeCoords 0x603ea0c for valid normal range inputs.

    Distances are from the whole occupied rectangle, not its pivot. With corner
    expansion disabled only four strips extending its sides are included.
    Returns a coordinate set; original HashSet iteration ordering is not exposed.
    """
    if minimum<0 or maximum<0:raise ValueError('Normal ranges must be nonnegative')
    result=area.coords() if minimum==0 else set()
    for distance in range(max(1,minimum),maximum+1):
        for z in range(area.bottom,area.top+1):
            result.add((area.left-distance,z));result.add((area.right+distance,z))
        for x in range(area.left,area.right+1):
            result.add((x,area.bottom-distance));result.add((x,area.top+distance))
        if include_diagonals:
            for dx in range(1,distance):
                dz=distance-dx
                for x in [area.left-dx,area.right+dx]:
                    result.add((x,area.bottom-dz));result.add((x,area.top+dz))
    return result


def effective_places(occupied,minimum,maximum,existing_coords):
    """CalculateEffectivePlaces normal AroundRange branch, after unit expansion."""
    places=around_range(occupied,minimum,maximum)
    if isinstance(existing_coords,MapGeometry):return {coord for coord in places if existing_coords.exists(coord)}
    return places&set(existing_coords)


def target_within_range(attacker_area,target_occupied,minimum,maximum,existing_coords):
    target_coords=target_occupied.coords() if isinstance(target_occupied,Area) else set(target_occupied)
    return bool(effective_places(attacker_area,minimum,maximum,existing_coords)&target_coords)


def place_set_distance(first,second):
    """MapPlaceSet.CalcMinDistance(set), 0x5f36a64, including overlap quirks."""
    from .original import i32
    a,b=Area.from_coords(first),Area.from_coords(second)
    def gap(lo,hi,other_lo,other_hi):
        x=i32(lo-other_hi);y=i32(other_lo-hi)
        x=i32(-x) if x<0 else x;y=i32(-y) if y<0 else y
        return x if (x&0xffffffff)<(y&0xffffffff) else y
    dx=gap(a.left,a.right,b.left,b.right)
    dz=gap(a.bottom,a.top,b.bottom,b.top)
    if a.left>b.right or b.left>a.right:
        return i32(dx+dz) if a.bottom>b.top or b.bottom>a.top else dx
    # Original takes this branch even when both bounding intervals overlap.
    return dz
