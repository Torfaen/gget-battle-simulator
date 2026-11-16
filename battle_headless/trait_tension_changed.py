"""Original timing18 wrapper; demand has no battle context, Always retains it."""


def execute_on_tension_changed(services,units,participation):
    units=tuple(units);calls=[];results=[]
    for unit in units:
        result=services.on_demand(18,[unit],None,owner_ids={unit['id']})
        calls.append(dict(unit_id=unit['id'],on_demand=result))
        results.extend(result['results'] or ())
    always=consumption=None
    if results:
        always=services.always(units,participation)
        consumption=services.consume(18,{r['owner_id'] for r in results},{r['runtime_trait_id'] for r in results})
    return dict(invocations=calls,results=results,always=always,consumption=consumption)
