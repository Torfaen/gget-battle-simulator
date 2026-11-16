"""MasterUnitSize pivot/strip footprint on the game's bounded integer grid.

Direction enum: None=0, Right=1, Up=2, Left=3, Down=4. The pivot is the
lower-left cell of its square, not the lower-left of the expanded bounding box.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class UnitFootprint:
    pivot: int
    front: int=0
    rear: int=0
    left: int=0
    right: int=0
    requires_turn: bool=False

    def __post_init__(self):
        if self.pivot<1 or min(self.front,self.rear,self.left,self.right)<0:
            raise ValueError('Positive pivot and nonnegative expansions required')

    @classmethod
    def from_row(cls,row):
        return cls(row['PivotSquareSize'],row['ForwardArea'],row['RearArea'],
            row['LeftArea'],row['RightArea'],row['RequiredTurnAround'])

    @property
    def width(self):return self.pivot+self.left+self.right

    @property
    def height(self):return self.pivot+self.front+self.rear

    def coords(self,origin=(0,0),direction=2):
        """_ExpandUnitPlace strips and quarter turns about the pivot-square center.

        Expanded corners stay empty. Integer doubled coordinates preserve the
        half-cell pivot of even-sized squares without introducing rounding.
        """
        if direction not in (0,1,2,3,4):raise ValueError('Unknown original direction')
        p=self.pivot
        cells={(x,z) for x in range(p) for z in range(-self.rear,p+self.front)}
        cells|={(x,z) for x in range(-self.left,p+self.right) for z in range(p)}
        pivot=p-1;result=set()
        for x,z in cells:
            dx,dz=2*x-pivot,2*z-pivot
            if direction==1:dx,dz=dz,-dx
            elif direction==3:dx,dz=-dz,dx
            elif direction==4:dx,dz=-dx,-dz
            result.add((origin[0]+(dx+pivot)//2,origin[1]+(dz+pivot)//2))
        return result

    def ordered_coords(self,origin=(0,0),direction=2):
        """Original pivot, front, left, right, rear insertion order."""
        if direction not in (0,1,2,3,4):raise ValueError('Unknown original direction')
        p=self.pivot
        cells=[]
        for x in range(p):
            for z in range(x,p):
                cells.append((x,z))
                if x!=z:cells.append((z,x))
        cells.extend((x,p+d) for d in range(self.front) for x in range(p))
        cells.extend((-1-d,z) for d in range(self.left) for z in range(p))
        cells.extend((p+d,z) for d in range(self.right) for z in range(p))
        cells.extend((x,-1-d) for d in range(self.rear) for x in range(p))
        result=[];pivot=p-1
        for x,z in cells:
            dx,dz=2*x-pivot,2*z-pivot
            if direction==1:dx,dz=dz,-dx
            elif direction==3:dx,dz=-dz,dx
            elif direction==4:dx,dz=-dx,-dz
            result.append((origin[0]+(dx+pivot)//2,origin[1]+(dz+pivot)//2))
        return result
