"""Original terrain preference and registered capability-change composition."""
from functools import cmp_to_key
from .original import i32

TERRAIN_PRIORITY={2:1,3:3,4:2,5:4,6:5}
TERRAIN_CHANGE_TYPES={104:(1,2),105:(2,3),106:(1,3)}


def change_record(trait):
    source,target=TERRAIN_CHANGE_TYPES.get(trait['TraitTypeIndex'],(1,1))
    return dict(target_terrain_type=i32(trait['TraitValue']),from_capability=source,
                to_capability=target,source_trait_id=trait['Id'])


def compare_preference(first,second):
    difference=i32(second[1]-first[1])
    return difference if difference else i32(TERRAIN_PRIORITY.get(first[0],6)-TERRAIN_PRIORITY.get(second[0],6))


def _values(values):
    # JSON object keys become strings when a captured formation is saved.
    return {kind:i32(values[kind] if kind in values else values[str(kind)]) for kind in range(2,7)}


def from_values(values):
    values=_values(values)
    rows=[(kind,value) for kind,value in values.items() if value!=1]
    if rows and max(value for _,value in rows)-min(value for _,value in rows)>2147483647:
        raise NotImplementedError('Original overflowing capability-pair List sort required')
    rows.sort(key=cmp_to_key(compare_preference))
    return dict(capabilities=values,sorted_standby_terrains=[kind for kind,value in rows])


def _replace_values(base_values,changes):
    base=_values(base_values);result=dict(base)
    for change in changes:
        kind=i32(change['target_terrain_type'])
        if kind in base and base[kind]==i32(change['from_capability']):
            result[kind]=i32(change['to_capability'])
    return result


def upgrade(base_values,option_parts_changes):
    """UserUnitStatus.GetUpgradedTerrainCapability, original-base matching."""
    return from_values(_replace_values(base_values,option_parts_changes or ()))


def compose(base_values,upgraded_values,corrections):
    """GetTerrainCapabilityByTrait: empty list uses upgraded status capability.

    A nonempty registered list starts from unupgraded base values; each source
    matches that same base rather than a previous source's output. Last match
    wins for a terrain. Capturing/registering the sources remains external.
    """
    if not corrections:return from_values(upgraded_values)
    return from_values(_replace_values(base_values,corrections))


def from_sources(sources):
    base=sources['base_values'];corrections=sources['corrections']
    if corrections:return compose(base,base,corrections)
    if 'upgraded_values' in sources:return from_values(sources['upgraded_values'])
    if 'option_parts_changes' not in sources:
        raise NotImplementedError('Original upgraded status or resolved option-parts changes required')
    return upgrade(base,sources['option_parts_changes'])


def for_unit(unit):
    sources=unit.get('terrain_capability_sources')
    if sources is None:raise NotImplementedError('Original terrain capability status and registered sources required')
    sources=dict(sources)
    if 'option_parts_changes' not in sources and 'option_parts_terrain_changes' in unit:
        sources['option_parts_changes']=unit['option_parts_terrain_changes']
    if 'corrections' not in sources:
        if unit.get('pending_terrain_capability_changes'):
            raise NotImplementedError('Resolve unsupported terrain capability sources before automatic composition')
        if 'registered_terrain_capability_changes' not in unit:
            raise NotImplementedError('Original terrain capability registration state required')
        sources['corrections']=unit['registered_terrain_capability_changes']
    return from_sources(sources)
