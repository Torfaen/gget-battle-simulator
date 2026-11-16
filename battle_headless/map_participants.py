"""MAP participant resource phase from an explicitly sorted target sequence.

Application builds a HashSet from targetUnits then adds the attacker. The
target DTO sorting service must run before this boundary; executor encounter
order is not silently substituted for the sorted target sequence.
"""
from .battle_resources import reflect_en_change,mp_change_info
from .weapon_use import reflect_mp_change
from .unit_life import is_alive


def participants(attacker,sorted_targets):
    result=[];seen=set()
    for unit in (*sorted_targets,attacker):
        if unit['id'] not in seen:
            seen.add(unit['id']);result.append(unit)
    return result


def reflect_resources(attacker,sorted_targets):
    """Call after unit-defeated traits and before tension-change traits.

    The native resource phase reflects resources on dead participants too;
    IsAlive is checked afterward for tension display/trait work.
    """
    ordered=participants(attacker,sorted_targets)
    # Missing snapshots indicate the preceding execution boundary was skipped.
    # Validate all participants before reflecting any of them.
    for unit in ordered:
        for key in ('en','max_en','mp','battle_start_mp'):
            if key not in unit:raise NotImplementedError('Original participant resource state missing: '+key)
    results=[]
    for unit in ordered:
        info=mp_change_info(unit)
        before=dict(en=unit['en'],mp=unit['mp'])
        applied_en=reflect_en_change(unit)
        reflect_mp_change(unit)
        results.append(dict(unit_id=unit['id'],mp_change=info,applied_en=applied_en,
            before=before,after=dict(en=unit['en'],mp=unit['mp']),alive=is_alive(unit)))
    return results


def reflect_execution_resources(env,attacker,execution,target_sort_type,origin,*,direction=2):
    """Sort completed DTO rows, then run the participant resource phase.

    Sort mode/origin are original view-service inputs, supplied explicitly;
    this entry point still belongs after unit-defeated traits in the use case.
    """
    from .map_target_sort import sort_results
    rows=sort_results(execution['results'],target_sort_type,origin,direction=direction)
    targets=[env.units[row['target']] for row in rows]
    return dict(sorted_results=rows,resource_results=reflect_resources(attacker,targets))


def reflect_service_resources(env,attacker,event):
    """Use the already twice-sorted actual service result after defeat traits."""
    if not event.get('executed') or 'service_sort_parameters' not in event['execution']:
        raise ValueError('Completed original MAP service result required')
    rows=event['execution']['results']
    return reflect_resources(attacker,[env.units[row['target']] for row in rows])
