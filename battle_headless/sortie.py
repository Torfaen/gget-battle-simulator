"""Original sortie terrain selection, given resolved unit/map inputs.

UnitTerrainCapability.GetSortieStatus 0x6180ef8; MapEntity.DecideSortieTerrain
0x5f33fa8; IngameUnitEntity.PrepareSortie 0x613ec80. Source construction and
map registration are separate; failed sorties must retain CannotSortie.
"""
from .original import i32
from .map_terrain import terrain_capability_value


def stage_terrain_types(stage_type):
    stage_type=i32(stage_type)
    choices={0:(0,),1:(2,),2:(3,),3:(4,3),4:(3,5,6),5:(4,3,5,6)}
    if stage_type not in choices:raise ValueError('Unknown original stage terrain type')
    return choices[stage_type]


def sortie_status(capability,stage_type):
    """0/1 permit sortie; 2 refuses. Use the upgraded user status capability.

    GroundAndSurface has a distinct branch, including an atmospheric > 2
    comparison. Other terrains test capability == 3, not >= 3.
    """
    stage_type=i32(stage_type)
    value=lambda kind:terrain_capability_value(capability,kind)
    if stage_type==5:
        if value(3)>2:return 0
        if value(3)==1 and (value(4)==1 or value(5)==value(6)==1):return 2
        if any(value(kind)==3 for kind in (4,5,4,6)):return 0
        return 1
    result=2
    for kind in stage_terrain_types(stage_type):
        current=value(kind)
        if current==3:return 0
        if current==2:result=1
    return result


def can_regard_terrain(chips,coords,terrain):
    """Native HashSet iteration order must be supplied for exception parity.

    All/Space/Ground require any occupied chip; others count every chip.
    A chip's All entry matches every requested terrain. Missing chips throw.
    """
    terrain=i32(terrain);coords=tuple(coords)
    def contains(coord):
        chip=chips[tuple(coord)]
        return 1 in chip or terrain in chip
    if terrain in (1,2,4):return any(contains(coord) for coord in coords)
    return sum(contains(coord) for coord in coords)==len(coords)


def decide_sortie_terrain(*,is_npc,stage_type,user_capability,current_standby_type,
                          standby_capability,chips,coords):
    # NPC identity bypasses the player's pre-sortie stage capability gate.
    if not is_npc and sortie_status(user_capability,stage_type)==2:return 0
    standby=i32(current_standby_type)
    if 1<=standby<=4:return standby+2
    for terrain in standby_capability['sorted_standby_terrains']:
        if can_regard_terrain(chips,coords,terrain):return i32(terrain)
    return 0


def prepare_sortie_changes(**inputs):
    """Ordered original property writes, including failed-sortie state.

    Success does not reset an existing existence state. Terrain None still
    sets UnitStandbyType.Land after setting UnitExistenceType.CannotSortie.
    """
    terrain=decide_sortie_terrain(**inputs)
    writes=[]
    if terrain==0:writes.append(('existence',9))
    writes.append(('standby_terrain_type',terrain))
    writes.append(('unit_standby_type',terrain-2 if 3<=terrain<=6 else 2))
    return writes
