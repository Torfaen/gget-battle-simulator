"""Original MAP attack orchestration with explicitly resolved combat inputs.

Context construction (terrain, passive activation, full weapon import) is not
inferred from ordinary weapons. Scalar math uses the pinned original kernel.
"""
from copy import deepcopy
from .original import i32
from .battle_evaluation import HitRate,evaluate_hit,evaluate_critical
from .hp_barrier import split_damage
from .weapon_resource_damage import mp_change_enabled
from .battle_resources import target_battle_resources


def calculate_attack(kernel,attacker,target,weapon,context,rng,*,hit_count=1,prediction=False):
    hit_spec=context['hit_rate']
    if not isinstance(hit_spec,HitRate):raise TypeError('Resolved original HitRate required')
    debuff_evaluator=context.get('debuff_evaluator')
    if debuff_evaluator is None and any(t['WeaponTraitTypeIndex']==12 for t in weapon.get('weapon_traits',[])):
        raise NotImplementedError('Supply the original MAP debuff evaluation service')
    # Resolve the supplied configurations before random evaluation.
    damage_inputs=dict(context['damage_inputs']);final_inputs=dict(context['final_inputs'])
    critical_rate=context['critical_rate'];tension=context['tension']
    en_value=context['en_attack_value'];mp_value=context['mp_attack_value']
    if prediction:
        attacker=deepcopy(attacker);target=deepcopy(target)
        if debuff_evaluator is not None:rng=deepcopy(rng)
    evaluations=[]
    class Evaluator:
        def evaluate(self,rate):
            enabled=rng.evaluate(rate)
            evaluations.append(dict(rate=rate,roll=getattr(rng,'last_roll',None)))
            return enabled
    evaluator=Evaluator()
    # MAP explicitly supplies broken=false and has no parry evaluation branch.
    hit=evaluate_hit(hit_spec.value,evaluator,sure_hit=hit_spec.sure_hit,prediction=prediction)
    critical_type=evaluate_critical(critical_rate,tension,evaluator,
        guaranteed=context.get('guaranteed_critical',False),prediction=prediction)
    critical=critical_type!=0
    if not critical:
        for field in ('critical_rate','critical_flat','critical_bonus'):final_inputs[field]=0
    final_inputs['hit_count']=i32(hit_count)
    battle_damage=kernel.battle_damage(**damage_inputs)
    raw_damage=kernel.final_damage(battle_damage,**final_inputs)
    hp_damage,barrier_damage=split_damage(target,weapon,raw_damage)
    en_damage=i32(en_value*hit_count) if en_value is not None else None
    # Native evaluation occurs after raw EN multiplication, only on a hit.
    debuffs=(debuff_evaluator(attacker,target,weapon,rng,prediction) if debuff_evaluator is not None else []) if hit else None
    mp_damage=i32(mp_value*hit_count) if mp_value is not None and mp_change_enabled(target) else None
    return dict(hit=hit,is_attack=True,hit_rate=hit_spec.value,critical_type=critical_type,
                hp_damage=hp_damage,hp_barrier_damage=barrier_damage,en_damage=en_damage,mp_damage=mp_damage,
                raw_damage=raw_damage,damage=raw_damage if hit else 0,battle_damage=battle_damage,
                has_damage_cut=bool(hit and final_inputs.get('damage_cut',False)),
                debuff_evaluated=hit,evaluated_debuffs=debuffs,random_evaluations=evaluations,
                prediction=prediction,hit_count=i32(hit_count),weapon_power=damage_inputs['power'])


def resolve_attack(env,attacker,weapon,impact_coords=(),*,prediction=False):
    if weapon['map_trait_category']!=1:raise ValueError('Attack MAP category required')
    context_service=getattr(env,'map_attack_context',None)
    if context_service is None:raise NotImplementedError('Original MAP combat-context construction is not connected')
    plan=env.map_weapon_plan(attacker,weapon,impact_coords)
    results=[]
    for row in plan['targets']:
        target=env.units[row['target']]
        with target_battle_resources(attacker,target,prediction=prediction):
            context=context_service(deepcopy(attacker) if prediction else attacker,
                deepcopy(target) if prediction else target,weapon,row,prediction)
            calculated=calculate_attack(env.rules.kernel,attacker,target,weapon,context,env.rng,
                hit_count=row['hit_count'],prediction=prediction)
            application=env.map_apply_result(target,calculated,prediction)
            from .map_action_mp import resolve_target_after_result
            after_result=resolve_target_after_result(env,attacker,target,weapon,calculated,prediction=prediction)
        results.append(dict(target=row['target'],target_coord=list(row['target_coord']),hit_count=row['hit_count'],calculation=calculated,application=application,context_trace=context.get('trace'),
            pending_weapon_debuff_grant='grant_weapon_debuff' in after_result['pending_lifecycle']))
        results[-1]['battle_action_mp_events']=after_result['mp_events']
        results[-1]['after_result']=after_result
    return dict(plan=plan,results=results,prediction=prediction,fees_applied=False,action_advanced=False)


def attach_map_attack(env):
    from .map_weapon_data import definition,properties
    from .map_weapon_costs import costs
    from .map_availability import availability,after_check
    from .map_moving import moving_after_check
    from .map_destination import move_destination
    from .map_terrain import moving_check
    from .terrain_capability import compose,from_sources,for_unit
    from .option_parts_terrain import parts_changes
    from .map_context import build_context
    from .map_drain import calculate_drained_value,apply_drained_value
    from .map_action import execute
    from .map_step import begin_step_resolution,finish_step_resolution,begin_post_battle_resolution,runtime_chance_step_lifecycle
    from .battle_resources import begin_battle,end_battle,reflect_en_change,mp_change_info
    from .map_participants import participants,reflect_resources
    from .map_target_sort import sort_results
    from .map_participants import reflect_execution_resources,reflect_service_resources
    from .regeneration import execute_map_regeneration_stage
    env.map_attack_profile='native-map-attack-resolved-inputs-subset'
    env.map_weapon_data_profile='native-master-map-level-and-property-subset'
    env.map_weapon_definition=lambda weapon_id,level,**options:definition(env.rules.master,weapon_id,level,**options)
    env.map_weapon_properties=properties
    env.map_weapon_costs=costs
    env.map_weapon_drained_value=calculate_drained_value
    env.map_apply_drained_value=apply_drained_value
    env.execute_map_weapon=lambda attacker,weapon,records,impact_coords=(),**context:execute(env,attacker,weapon,records,impact_coords,**context)
    env.map_weapon_begin_step_resolution=begin_step_resolution
    env.map_weapon_finish_step_resolution=lambda unit,step,lifecycle=None:finish_step_resolution(
        unit,step,lifecycle=runtime_chance_step_lifecycle(env,lifecycle))
    env.map_weapon_begin_post_battle_resolution=lambda attacker,event,trait_sets,current_phase,step,**options:begin_post_battle_resolution(
        env,attacker,event,trait_sets,current_phase,step,**options)
    env.begin_battle_resources=begin_battle
    env.end_battle_resources=end_battle
    env.reflect_en_change=reflect_en_change
    env.mp_change_since_battle_start=mp_change_info
    env.map_weapon_participants=participants
    env.map_weapon_reflect_participant_resources=reflect_resources
    env.map_weapon_sort_results=sort_results
    env.map_weapon_reflect_execution_resources=lambda attacker,execution,target_sort_type,origin,**options:reflect_execution_resources(env,attacker,execution,target_sort_type,origin,**options)
    env.map_weapon_reflect_service_resources=lambda attacker,event:reflect_service_resources(env,attacker,event)
    env.map_weapon_regeneration_stage=lambda attacker,event,trait_sets,current_phase:execute_map_regeneration_stage(
        env,env.rules.master,trait_sets,attacker,event,current_phase)
    env.map_cost_profile='native-explicit-level-and-registered-corrections'
    env.map_availability_profile='native-before-check-and-explicit-moving-after-check'
    env.map_weapon_availability=lambda unit,weapon,records,**context:availability(env,unit,weapon,records,**context)
    env.map_weapon_after_check=after_check
    env.map_weapon_moving_after_check=moving_after_check
    env.map_moving_profile='native-standby-occupancy-and-shooting-wall-service-order'
    env.map_weapon_move_destination=lambda unit,weapon,**context:move_destination(env,unit,weapon,**context)
    env.map_destination_profile='native-cardinal-destination-nonoverflowing-range-sort'
    env.map_weapon_moving_check=lambda unit,weapon,**context:moving_check(env,unit,weapon,**context)
    env.map_terrain_profile='native-resolved-chip-selection-and-wall-capability'
    env.unit_terrain_capability=compose
    env.unit_terrain_capability_from_sources=from_sources
    env.unit_terrain_capability_for_unit=for_unit
    env.option_parts_terrain_changes=lambda part_ids:parts_changes(env.rules.master,part_ids)
    if not hasattr(env,'map_attack_context'):
        env.map_attack_context=lambda attacker,target,weapon,row,prediction:build_context(env,attacker,target,weapon,row,prediction)
        env.map_context_profile='native-registered-traits-and-bound-terrain-subset'
    elif not hasattr(env,'map_context_profile'):env.map_context_profile='external-context-service'
    env.map_weapon_attack=lambda attacker,weapon,impact_coords=(),prediction=False:resolve_attack(env,attacker,weapon,impact_coords,prediction=prediction)
