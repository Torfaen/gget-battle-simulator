"""Ordinary first chance attempt and final retry at distinct caller boundaries.

First: after MP, before movement consumption/score/end traits. Final: after
resource reflection and tension18, before map removal. The caller still owns
the intervening stages. Presentation trait DTOs are deliberately excluded.
"""
from copy import copy,deepcopy
from dataclasses import asdict
from .turns import _unit_state
from .original import i32
from .battle_user_records import entity_is_alive,unit_existence,unit_side
from .trait_consumption import consume_guaranteed_chance_step


def try_earn_chance_step(services,actor,participation,step,target_defeated):
    earned,timing=services.try_use(actor,target_defeated)
    result=dict(earned=earned,consumed_timing=timing,consumption=None)
    if earned:
        step['has_earned_chance_step']=True
        step['display_chance_step_trait']=services.display(participation.main_attacker_unit,target_defeated)
        if timing in (15,23):result['consumption']=services.consume(actor,timing)
        services.record(participation.main_attacker_unit)
    return result


def execute_first_chance(services,actor,participation,step):
    guard=participation.support_defender_unit
    defeated=(guard is not None and not services.alive(guard)) or not services.alive(participation.main_target_unit)
    services.evaluate_move(actor)
    result=try_earn_chance_step(services,actor,participation,step,defeated)
    if not result['earned']:
        step['has_earned_chance_step']=False;step['display_chance_step_trait']=None
    return dict(result,target_defeated=defeated)


def execute_final_chance(services,actor,participation,step,target_defeated):
    if step['has_earned_chance_step']:return dict(earned=True,retried=False,ended_attack=False)
    result=try_earn_chance_step(services,actor,participation,step,target_defeated)
    if not result['earned']:services.end_attack(actor)
    return dict(result,retried=True,ended_attack=not result['earned'])


class FixedStageChanceStep:
    """Shared canonical units/groups/records, with per-boundary preflight.

    No legacy acted/moved resets or invented existence transition on earning a
    chance step. EndAttackStep alone may change existence. Source getter/setter
    storage is represented by the existing chance_step fields. Exceptions are
    preflighted, not a reproduction of the client's partial failure state.
    """
    def __init__(self,calculation,records,step=None):
        self.calculation=calculation;self.runtime=calculation.runtime;self.records=records
        self.step=step if step is not None else dict(has_earned_chance_step=False,display_chance_step_trait=None)
        self.pending=None;self.finished=False;self.events=[]

    def begin(self,actor,participation):return self._run('first',actor,participation)
    def finish(self,actor,participation):return self._run('final',actor,participation)

    def _run(self,kind,actor,p):
        if kind=='first' and self.pending is not None:raise ValueError('First chance attempt already completed')
        if kind=='final' and (self.pending is None or self.finished):raise ValueError('Unfinished first chance attempt required')
        if kind=='final' and (actor is not self.pending['actor'] or p is not self.pending['participation']):
            raise ValueError('Same actor and participation required for final chance retry')
        for u in (actor,p.main_attacker_unit,p.main_target_unit,p.support_defender_unit):
            if u is not None and self.runtime.units.get(u['id']) is not u:raise ValueError('Canonical chance-step units required')
        units,groups,a,trial_p,records,step,pending=deepcopy((self.runtime.units,self.runtime.groups,actor,p,
            self.records,self.step,self.pending))
        runtime=copy(self.runtime);runtime.units=units;runtime.groups=groups
        calculation=copy(self.calculation);calculation.runtime=runtime
        trial=FixedStageChanceStep(calculation,records,step);trial.pending=pending
        trial._execute(kind,a,trial_p)
        return self._execute(kind,actor,p)

    def _execute(self,kind,actor,p):
        self.events=[]
        if kind=='first':
            result=execute_first_chance(self,actor,p,self.step)
            self.pending=dict(actor=actor,participation=p,target_defeated=result['target_defeated'])
        else:
            result=execute_final_chance(self,actor,p,self.step,self.pending['target_defeated']);self.finished=True
        return dict(result,events=self.events,step=deepcopy(self.step),user_records=self.records.snapshot(),
            presentation_excluded=True)

    def state(self,u):
        state=_unit_state(u);state.existence=unit_existence(u);return state

    def alive(self,u):
        self.events.append(['alive',u['id']]);return entity_is_alive(unit_existence(u))

    def evaluate_move(self,u):
        self.events.append(['evaluate_move',u['id']])
        u['chance_step']['can_move_after_attack']=i32(u.get('scalar_corrections',{}).get('PostAttackMoveCount',0))>0

    def try_use(self,u,defeated):
        self.events.append(['try_use',u['id'],defeated]);state=self.state(u)
        result=state.try_weapon_used(defeated);u['chance_step']=asdict(state);return result

    def display(self,u,defeated):
        self.events.append(['display_excluded',u['id'],defeated]);return None

    def consume(self,u,timing):
        self.events.append(['consume_wrapper',timing,u['id']])
        return consume_guaranteed_chance_step(self.runtime.groups,self.runtime.units.values(),
            self.calculation.phase,u['id'],timing)

    def record(self,u):
        self.events.append(['record',4,unit_side(u)]);self.records.increment(4,unit_side(u))

    def end_attack(self,u):
        self.events.append(['end_attack',u['id']]);state=self.state(u);state.end_attack_step()
        u['chance_step']=asdict(state)
        if 'existence' in u:u['existence']=state.existence
