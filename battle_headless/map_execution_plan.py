"""Original MAP target collection before damage, costs and action scheduling."""
from .original import i32
from .map_ranges import parse_custom_range,translate_range,effect_coords
from .map_targets import select_range_targets,select_whole_map_targets


def collect_impact_targets(env,attacker,weapon,impact_coords):
    """Per-impact unit sets, then first-encounter order and Int32 hit counts."""
    impacts=list(impact_coords)
    if not impacts:raise ValueError('Original ImpactRange requires at least one impact coordinate')
    offsets=parse_custom_range(weapon['effect_range'])
    ranges=[];targets={}
    for origin in impacts:
        # The executor translates the raw template; it does not call _FilterRange.
        coords=translate_range(offsets,origin)
        ranges.append(dict(impact_coord=list(origin),effect_coords=[list(c) for c in coords]))
        for row in select_range_targets(env,attacker,coords,weapon['map_trait_category']):
            identity=row['target']
            if identity not in targets:targets[identity]=dict(row,hit_count=0)
            targets[identity]['hit_count']=i32(targets[identity]['hit_count']+1)
    return dict(effect_ranges=ranges,targets=list(targets.values()))


def prepare_execution(env,attacker,weapon,impact_coords=()):
    kind=weapon['map_range_type']
    if kind==2:return collect_impact_targets(env,attacker,weapon,impact_coords)
    if kind==5:
        return dict(effect_ranges=None,targets=select_whole_map_targets(env,attacker,weapon['map_trait_category']))
    if kind==6:
        coords=effect_coords(env,attacker,weapon)
        ranges=[dict(impact_coord=None,effect_coords=[list(c) for c in coords])]
    elif kind in (1,3,4):
        impacts=iter(impact_coords)
        try:origin=next(impacts)
        except StopIteration:raise ValueError('Original range MAP requires an origin coordinate') from None
        coords=effect_coords(env,attacker,weapon,origin)
        ranges=[dict(impact_coord=list(origin),effect_coords=[list(c) for c in coords])]
    else:raise NotImplementedError('Unknown original MAP execution variant')
    return dict(effect_ranges=ranges,targets=select_range_targets(env,attacker,coords,weapon['map_trait_category']))


def attach_map_execution_plan(env):
    env.map_execution_plan_profile='native-map-target-collection-subset'
    env.map_weapon_plan=lambda attacker,weapon,impact_coords=():prepare_execution(env,attacker,weapon,impact_coords)
    from .map_result_runtime import attach_map_result_runtime
    attach_map_result_runtime(env)
