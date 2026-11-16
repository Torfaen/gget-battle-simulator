"""MAP use-case chance-step boundaries, separate from damage/trait execution.

Native first evaluates MAP-after-move permission after regeneration, then tries
guaranteed chance step. After defeat/MP/tension traits it retries only if no
chance step was earned, and calls EndMapWeaponAttackStep only if both fail.
"""
from dataclasses import asdict
from .turns import _unit_state
from .original import i32


def evaluate_map_move_after_attack(unit):
    """EvaluateCanMoveAfterMapWeaponAttack / TraitCorrection getter 0x5f9fd20."""
    return i32(unit.get('scalar_corrections',{}).get('PostMapWeaponAttackMoveCount',0))>0


def runtime_chance_step_lifecycle(env,lifecycle=None):
    hooks=dict(lifecycle or {})
    consume=getattr(env,'consume_runtime_guaranteed_chance_step',None)
    if consume is not None:hooks.setdefault('consume_on_guaranteed_chance_step_used',consume)
    return hooks


def begin_post_battle_resolution(env,attacker,event,trait_sets,current_phase,step,*,lifecycle=None):
    """After battle-end traits: regeneration -> move permission -> first chance.

    Later always/defeat/tension/resource/activity phases remain separate.
    """
    from .regeneration import execute_map_regeneration_stage
    _unit_state(attacker)  # Require original step state before healing anyone.
    if 'first_chance_step' in event.get('post_battle_stages',{}):
        raise ValueError('MAP first chance-step stage already executed')
    regeneration=execute_map_regeneration_stage(env,env.rules.master,trait_sets,
        attacker,event,current_phase)
    chance=begin_step_resolution(attacker,step,
        can_map_weapon_move_after_attack=evaluate_map_move_after_attack(attacker),
        lifecycle=runtime_chance_step_lifecycle(env,lifecycle))
    event['post_battle_stages']['first_chance_step']=chance
    return dict(regeneration=regeneration,first_chance_step=chance)


def try_guaranteed_step(unit,step,*,lifecycle=None):
    """MapWeaponExecuteUseCase._TryEarnGuaranteedChanceStep 0x5b2d3d4."""
    state=_unit_state(unit);earned,timing=state.try_guaranteed()
    unit['chance_step']=asdict(state)
    result=dict(earned=earned,consumed_timing=timing,pending_lifecycle=[])
    if earned:
        step['has_earned_chance_step']=True
        hooks=lifecycle or {}
        for name in ('find_display_trait_on_chance_step_earned','consume_on_guaranteed_chance_step_used'):
            if name in hooks:
                value=hooks[name](unit,timing)
                if name=='find_display_trait_on_chance_step_earned':step['display_chance_step_trait']=value
                else:result['trait_consumption']=value
            else:result['pending_lifecycle'].append(name)
    return result


def begin_step_resolution(unit,step,*,can_map_weapon_move_after_attack,lifecycle=None):
    """Call after battle-end/regeneration traits, before later trait refreshes."""
    unit['chance_step']['can_move_after_map_attack']=bool(can_map_weapon_move_after_attack)
    return try_guaranteed_step(unit,step,lifecycle=lifecycle)


def finish_step_resolution(unit,step,*,lifecycle=None):
    """Call after defeat/MP/tension traits. Does not invent those services."""
    result=dict(earned=bool(step.get('has_earned_chance_step')),pending_lifecycle=[])
    if not result['earned']:result=try_guaranteed_step(unit,step,lifecycle=lifecycle)
    if not result['earned']:
        state=_unit_state(unit);state.end_map_weapon_attack_step()
        unit['chance_step']=asdict(state)
    unit['existence']=unit['chance_step']['existence']
    unit['acted']=unit['existence']!=0
    return result
