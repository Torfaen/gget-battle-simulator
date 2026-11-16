"""Original timing20 dispatch, independent of escape and final resource reflection."""
from .original import i32
from .battle_sides import is_ally
from .battle_user_records import entity_is_alive,unit_existence,unit_side


def matches_defeated_condition(condition,owner_side,target,*,main_unit_id,character_id):
    ally=is_ally(owner_side,unit_side(target));kind=i32(condition['AllyTypeIndex'])
    if (kind==1 and not ally) or (kind==2 and ally):return False
    main_ids={int(v) for v in condition['MainUnitIds'].split(',') if v}
    character_ids={int(v) for v in condition['CharacterIds'].split(',') if v}
    if not main_ids and not character_ids:return True
    main_matches=main_unit_id(target) in main_ids if main_ids else False
    # Both getters run when both filters exist, even after a main-ID mismatch.
    character_matches=character_id(target) in character_ids if character_ids else False
    return (main_matches and character_matches) if main_ids and character_ids else main_matches or character_matches


class MasterDefeatedConditions:
    def __init__(self,master):self.master=master

    def main_unit_id(self,unit):return self.master.index['m_unit'][unit['unit_id']]['MainUnitId']

    def character_id(self,unit):
        if 'account_configuration' in unit:identity=unit['account_configuration']['character_id']
        else:identity=unit['pilot']['CharacterId']
        return self.master.index['m_character'][identity]['Id']

    def count(self,trait,owner_side,defeated):
        row=self.master.index['m_trait'].get(trait['master_trait_id'])
        if row is None or not row['ActionTimingTargetValue']:return 0
        condition=self.master.index['m_trait_action_timing_unit_defeated_condition'].get(row['ActionTimingTargetValue'])
        if condition is None:return 0
        return i32(sum(matches_defeated_condition(condition,owner_side,u,
            main_unit_id=self.main_unit_id,character_id=self.character_id) for u in defeated))


def execute_on_unit_defeated(services,units):
    """Snapshot matching traits, then repeat each singleton dispatch and consume.

    IsAlive is an existence predicate. Demand receives no participation. A
    nonnull empty result still calls consumption with empty filters; null does
    not. Counts are computed once per trait, before that trait's repeated loop.
    """
    defeated=list({u['id']:u for u in units if not entity_is_alive(unit_existence(u))}.values())
    output=dict(defeated_ids=[u['id'] for u in defeated],selected_runtime_trait_ids=[],invocations=[],results=[])
    if not defeated:return output
    traits=list(services.traits(20));output['selected_runtime_trait_ids']=[t['runtime_trait_id'] for t in traits]
    for trait in traits:
        owner=services.owner(trait['owner_unit_id'])
        if owner is None or not entity_is_alive(unit_existence(owner)):continue
        count=services.count(trait,unit_side(owner),defeated)
        for index in range(max(0,count)):
            result=services.on_demand([trait],20,[owner],None);rows=result['results'];consumption=None
            if rows is not None:
                output['results'].extend(rows)
                consumption=services.consume(20,{r['owner_id'] for r in rows},{r['runtime_trait_id'] for r in rows})
            output['invocations'].append(dict(owner_id=owner['id'],runtime_trait_id=trait['runtime_trait_id'],
                match_index=index,on_demand=result,consumption=consumption))
    return output
