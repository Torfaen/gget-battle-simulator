"""Original chip terrain selection and wall predicate from resolved map state."""
from .original import i32


def chip_terrain_data(chip,sorted_standby_terrains,standby_type):
    # Original Count is checked before trying the currently selected terrain.
    if not sorted_standby_terrains:return None
    if standby_type in chip:return chip[standby_type]
    for terrain in sorted_standby_terrains:
        if 4<=i32(terrain)<=6 and terrain in chip:return chip[terrain]
    return None


def terrain_capability_value(capability,terrain_type):
    terrain_type=i32(terrain_type)
    if terrain_type==1:
        preferred=capability['sorted_standby_terrains']
        if not preferred:return 1
        if i32(preferred[0])==1:raise ValueError('Recursive All terrain in original preference state')
        return terrain_capability_value(capability,preferred[0])
    if terrain_type not in (2,3,4,5,6):return 1
    return i32(capability['capabilities'][terrain_type])


def can_stay(capability,terrain_type):
    return terrain_capability_value(capability,terrain_type)!=1


class MapTerrainRuntime:
    """Resolved chip dictionary, original existence geometry and capability DTO.

    Capability values/preference order must be captured or resolved by original
    sources. Dynamic motif merging and live trait activation are not guessed.
    """
    def __init__(self,chips,geometry):
        self.chips=chips;self.geometry=geometry

    @classmethod
    def from_stage(cls,stage,geometry):
        chips={}
        for coord,rows in stage.terrain.items():
            chip={}
            for row in rows:
                kind=row['TerrainTypeIndex']
                if kind in chip:raise NotImplementedError('Resolve overlapping terrain motifs before wall checks')
                chip[kind]=row
            chips[tuple(coord)]=chip
        return cls(chips,geometry)

    def contain_wall(self,coord,standby_type,capability):
        coord=tuple(coord)
        if not self.geometry.exists(coord):return True
        chip=self.chips.get(coord)
        if chip is None:return True
        terrain=chip_terrain_data(chip,capability['sorted_standby_terrains'],standby_type)
        if terrain is None:return True
        return not can_stay(capability,terrain['TerrainTypeIndex'])

    def contain_walls(self,places,standby_type,capability):
        return any(self.contain_wall(coord,standby_type,capability) for coord in places)


def moving_check(env,unit,weapon,*,origin,standby_terrain_type,occupancy,terrain_capability=None,terrain_runtime=None):
    from .map_destination import move_destination
    from .map_ranges import direction_coord,parse_custom_range,translate_range
    from .map_moving import moving_after_check
    if terrain_capability is None:
        from .terrain_capability import for_unit
        terrain_capability=for_unit(unit)
    if terrain_runtime is None:
        stage=getattr(env,'_map_context_stage',None)
        if stage is None:raise NotImplementedError('Resolved original map chips required')
        terrain_runtime=MapTerrainRuntime.from_stage(stage,env.battle_map.geometry)
    destination=move_destination(env,unit,weapon,origin=origin)
    destination_places=env.battle_map.footprints[unit['id']].ordered_coords(destination,unit['direction'])
    shooting=translate_range((direction_coord(c,unit['direction']) for c in parse_custom_range(weapon['shooting_range'])),origin)
    def find_ids(places):
        return {occupancy[tuple(coord)] for coord in places if tuple(coord) in occupancy}
    reasons=moving_after_check(unit['id'],destination_places,shooting,standby_terrain_type,terrain_capability,
        contain_walls=terrain_runtime.contain_walls,find_unit_ids=find_ids)
    return dict(destination=list(destination),destination_places=[list(c) for c in destination_places],
        shooting_coords=[list(c) for c in shooting],reasons=list(map(int,reasons)),
        available_after_check=not reasons,complete_action_eligibility=False)
