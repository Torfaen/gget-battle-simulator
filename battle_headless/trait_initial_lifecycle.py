"""Original Always/StageStart/FirstTurnStart orchestration over explicit services.

The services supply original repository order, condition checks and concrete
effects. This layer never substitutes default activation/recipient decisions.
"""


def execute_always_on_battle(services,units,battle_context=None):
    units=tuple(units)
    context=services.create_context(battle_context)
    executed=[];remove_attempts=[];aura_state=[None]
    for trait in services.always_traits():
        identity=trait['runtime_trait_id']
        if not services.active(trait,context,battle_context):
            for unit in units:
                services.remove(trait,unit)
                remove_attempts.append((identity,unit['id']))
            continue
        # Native does the parent lookup for each recipient, after activation.
        for unit in units:
            parent=services.parent(identity)
            if parent is None:continue
            if parent['source_type']==8 and parent['owner_unit_id']!=unit['id']:continue
            if not services.target(trait,unit,context,battle_context):
                services.remove(trait,unit)
                remove_attempts.append((identity,unit['id']))
                continue
            if services.is_aura(parent) and not services.apply_aura(parent,trait,unit,aura_state):continue
            boost=services.boost_count(trait,parent['owner_unit_id'])
            services.execute(trait,unit,boost)
            executed.append((identity,unit['id']))
    services.return_contexts()
    return dict(executed=executed,remove_attempts=remove_attempts)


def execute_on_stage_start(services,units):
    units=tuple(units)
    always=services.always(units,None)
    demand=services.on_demand(1,units,None)
    consumption=services.consume(1,None,None)
    return dict(always=always,on_demand=demand,consumption=consumption)


def execute_on_first_turn_start(services,units):
    units=tuple(units)
    always=services.always(units,None)
    demand=services.on_demand(26,units,None)
    for unit in units:services.reflect_mp(unit)
    consumption=services.consume(26,None,None)
    return dict(always=always,on_demand=demand,consumption=consumption)


def execute_on_own_phase_start(services,units):
    """Service wrapper 0x5f8b50c; the caller consumes returned IDs later."""
    return services.on_demand(2,tuple(units),None)


def consume_own_phase_results(services,results):
    """_ChangeStatus's result-ID selection and timing2 consumption.

    Includes ineffective results, deduplicates identities across recipients,
    and retains an empty filter when no concrete result was returned. The
    surrounding EN/status reset and visual accumulator are separate steps.
    """
    identities={result['runtime_trait_id'] for result in results or ()}
    return services.consume(2,None,identities)


def execute_immediate_hit_event(services,unit,timing,battle_context):
    """ExecuteOnAnyWeaponHit/ExecuteOnDamaged: demand -> consume -> Always."""
    if timing not in (11,12):raise ValueError('Immediate hit timing required')
    demand=services.on_demand(timing,[unit],battle_context,owner_ids={unit['id']})
    identities={result['runtime_trait_id'] for result in demand['results'] or ()}
    consumed=services.consume(timing,{unit['id']} if timing==12 else None,identities)
    always=services.always([unit],battle_context)
    return dict(on_demand=demand,consumption=consumed,always=always)
