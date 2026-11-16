"""Original end-of-exchange trait wrappers, with explicit dependency services.

These are individual original service boundaries, not the outer AttackUseCase
schedule. Target iteration is supplied by the caller; visual summaries are
omitted. None of these wrappers invokes Always or reflects pending MP.
"""
from .battle_sides import is_alive_on_map


def execute_after_hit_event(services,unit,timing,participation):
    if timing not in (10,13):raise ValueError('Battle-end damaged/hit timing required')
    demand=services.on_demand(timing,[unit],participation,owner_ids={unit['id']})
    # Both original wrappers consume by owner even with null/empty results.
    consumption=services.consume(timing,{unit['id']},None)
    return dict(on_demand=demand,consumption=consumption)


def execute_on_battle_end(services,units,participation):
    # The original creates a filtered HashSet from a caller-supplied HashSet.
    # Preserve the supplied iteration order without claiming CLR enumeration.
    selected={}
    for unit in units:
        if is_alive_on_map(services.existence(unit)):selected.setdefault(unit['id'],unit)
    targets=list(selected.values())
    result=dict(target_ids=list(selected),on_demand=None,consumption=None)
    if not targets:return result
    owners=set(selected)
    demand=services.on_demand(22,targets,participation,owner_ids=owners)
    result['on_demand']=demand
    # Unlike immediate-hit and own-phase wrappers, null/empty results return
    # before consumption. Ineffective but present results still supply IDs.
    if not demand['results']:return result
    identities={row['runtime_trait_id'] for row in demand['results']}
    result['consumption']=services.consume(22,owners,identities)
    return result


def consume_battle_end_action(services,owner_id,timing):
    if timing==8:return services.consume(8,{owner_id},None)
    if timing not in (9,17):raise ValueError('Movement/evasion/support-defense timing required')
    identity=services.oldest(owner_id,timing)
    if identity is None:return None
    return services.consume(timing,{owner_id},{identity})


def execute_on_regeneration(services,unit,participation):
    """ExecuteOnRegeneration gameplay boundary; visual DTOs are omitted.

The wrapper itself does not filter existence or HP. Actual demand conditions
and count-consumption gates are evaluated by their respective services.
"""
    demand=services.on_demand(14,[unit],participation,owner_ids={unit['id']})
    consumption=None
    if demand['results']:consumption=services.consume(14,{unit['id']},None)
    return dict(on_demand=demand,consumption=consumption)
