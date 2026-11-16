"""Gameplay portion of AttackUseCase._ChangeMpAsync over shared units.

Receives the original outer results and final participation. Strike selection,
async presentation/cancellation and later MP reflection are separate phases.
"""
from copy import deepcopy
from dataclasses import dataclass,field
from .original import i32
from .ordinary_attack_mp import AttackMpChangeValues,queue_attack_mp
from .map_action_mp import queue_battle_action_mp
from .battle_user_records import entity_is_alive,unit_existence


@dataclass(frozen=True)
class StrikeMpResult:
    """Immutable gameplay projection of an original outer strike-result DTO.

    actor/guard retain canonical entity identity; numerical fields are captured
    values, independent of later calculation DTO or participation changes.
    """
    action: str
    actor: dict
    guard: object
    hit_kind: int
    hp_damage: int
    hp_barrier_damage: int
    action_inability: bool=False
    attack_inability: bool=False
    hp_low: bool=False
    en_low: bool=False

    @property
    def can_act(self):return not (self.action_inability or self.attack_inability or self.hp_low or self.en_low)
    @property
    def is_hit(self):return i32(self.hit_kind)==1
    @property
    def hp_total_damage(self):return i32(self.hp_damage+self.hp_barrier_damage)

    @classmethod
    def completed(cls,action,actor,guard,result):
        if result.is_action_inability or result.is_attack_inability:
            raise NotImplementedError('Outer skipped-result construction must resolve original resource/inability flags')
        return cls(action,actor,guard,result.raw['hit_result'].kind,result.hit_hp_damage,result.hit_hp_barrier_damage)

    @classmethod
    def skipped(cls,action,actor,*,action_inability=False,attack_inability=False,hp_low=False,en_low=False):
        if action not in ('main_attack','counter'):raise ValueError('Only main/counter have original skipped-result constructors')
        return cls(action,actor,None,0,0,0,bool(action_inability),bool(attack_inability),bool(hp_low),bool(en_low))

    def snapshot(self):
        return dict(action=self.action,actor=self.actor['id'],guard=None if self.guard is None else self.guard['id'],
                    hit_kind=self.hit_kind,hp_damage=self.hp_damage,hp_barrier_damage=self.hp_barrier_damage,
                    action_inability=self.action_inability,attack_inability=self.attack_inability,hp_low=self.hp_low,en_low=self.en_low)


@dataclass
class ExchangeStrikeResults:
    normal_main: object=None
    preemptive_main: object=None
    normal_supports: list=field(default_factory=list)
    preemptive_supports: list=field(default_factory=list)
    normal_counter: object=None
    preemptive_counter: object=None
    normal_counter_supports: list=field(default_factory=list)
    preemptive_counter_supports: list=field(default_factory=list)

    def record(self,result,*,preemptive=False):
        suffix={'main_attack':'main','counter':'counter','support_attack':'supports','counter_support':'counter_supports'}[result.action]
        name=('preemptive_' if preemptive else 'normal_')+suffix
        if suffix in ('main','counter'):
            if getattr(self,name) is not None:raise ValueError('Original main/counter result slot already populated')
            setattr(self,name,result)
        else:getattr(self,name).append(result)

    def all_results(self):
        for value in vars(self).values():
            if isinstance(value,list):yield from value
            elif value is not None:yield value

    def snapshot(self):
        return {key:([r.snapshot() for r in value] if isinstance(value,list) else None if value is None else value.snapshot())
                for key,value in vars(self).items()}


def queue_defeat_mp(values,p,side):
    """One invocation per defeated main/guard; no alive filtering or dedup."""
    if side==1:
        main=p.main_attacker_unit;guard=p.attack_side_support_defender_unit;supporters=p.support_attack_units
        opponent=p.main_target_unit;opponent_guard=p.support_defender_unit;opponent_supporters=p.counter_support_units
        main_kill=True
    elif side==2:
        main=p.main_target_unit;guard=p.support_defender_unit;supporters=p.counter_support_units
        opponent=p.main_attacker_unit;opponent_guard=p.attack_side_support_defender_unit;opponent_supporters=p.support_attack_units
        main_kill=p.counter_selection==1
    else:raise ValueError('Original attacking side must be 1 or 2')
    events=[]
    def queue(unit,kind):
        value=i32(getattr(values,kind));delta=queue_battle_action_mp(unit,value)
        events.append(dict(unit_id=unit['id'],kind=kind,value=value,attack_failure=False,queued_mp=delta))
    if main_kill:queue(main,'main_attack_kill')
    queue(main,'any_attack_kill')
    if guard is not None:queue(guard,'any_attack_kill')
    for unit in supporters:queue(unit,'any_attack_kill')
    queue(opponent,'ally_killed')
    if opponent_guard is not None:queue(opponent_guard,'ally_killed')
    for unit in opponent_supporters:queue(unit,'ally_killed')
    return events


def queue_battle_mp(values,p,results,*,step_counter_type):
    """Original side1 rewards/results, then side2 rewards/results ordering."""
    events=[];steps=[]
    def defeated(unit,side):
        if unit is None:return
        steps.append(['alive',unit['id']])
        if not entity_is_alive(unit_existence(unit)):
            steps.append(['defeat',side]);events.extend(queue_defeat_mp(values,p,side))
    def attack(result,actor,protected):
        steps.append(['attack',actor['id'],protected['id'],None if result.guard is None else result.guard['id'],
                      result.is_hit,result.hp_total_damage])
        events.extend(queue_attack_mp(values,actor,protected,result.guard,is_hit=result.is_hit,hp_total_damage=result.hp_total_damage))
    defeated(p.main_target_unit,1);defeated(p.support_defender_unit,1)
    if results.normal_main is not None and results.normal_main.can_act:attack(results.normal_main,p.main_attacker_unit,p.main_target_unit)
    # Original preemptive main caller only checks nonnull.
    if results.preemptive_main is not None:attack(results.preemptive_main,p.main_attacker_unit,p.main_target_unit)
    for group in (results.normal_supports,results.preemptive_supports):
        for result in group:attack(result,result.actor,p.main_target_unit)
    defeated(p.main_attacker_unit,2);defeated(p.attack_side_support_defender_unit,2)
    for result in (results.normal_counter,results.preemptive_counter):
        if result is not None:
            steps.append(['counter_selection',i32(step_counter_type)])
            if i32(step_counter_type)==1 and result.can_act:attack(result,p.main_target_unit,p.main_attacker_unit)
    for group in (results.normal_counter_supports,results.preemptive_counter_supports):
        for result in group:attack(result,result.actor,p.main_attacker_unit)
    return dict(events=events,steps=steps)


class FixedStageMpSettlement:
    """Explicit post-strike MP phase; preflight retains shared identity."""
    def __init__(self,calculation):
        self.calculation=calculation;self.runtime=calculation.runtime
        self.values=AttackMpChangeValues.from_master(calculation.master)

    def settle(self,participation,results,*,step_counter_type):
        p=participation
        if p.main_attacker_unit is None or p.main_target_unit is None:raise ValueError('Original main participants required')
        for name,value in vars(results).items():
            action=('counter_support' if name.endswith('counter_supports') else 'support_attack' if name.endswith('supports')
                    else 'main_attack' if name.endswith('main') else 'counter')
            for result in value if isinstance(value,list) else () if value is None else (value,):
                if result.action!=action:raise ValueError('Outer result does not match its original slot')
                owner=p.main_attacker_unit if action=='main_attack' else p.main_target_unit if action=='counter' else result.actor
                if result.actor is not owner:raise ValueError('Outer main/counter result actor mismatch')
        units=[*p.participants,p.main_attacker_unit,p.main_target_unit,p.support_defender_unit,
               p.attack_side_support_defender_unit,*p.support_attack_units,*p.counter_support_units]
        for result in results.all_results():units.extend((result.actor,result.guard))
        for unit in units:
            if unit is not None and self.runtime.units.get(unit['id']) is not unit:raise ValueError('Canonical shared settlement units required')
        trial_p,trial_results=deepcopy((p,results))
        queue_battle_mp(self.values,trial_p,trial_results,step_counter_type=step_counter_type)
        before={key:u.get('pending_mp_change',0) for key,u in self.runtime.units.items()}
        value=queue_battle_mp(self.values,p,results,step_counter_type=step_counter_type)
        value.update(before=before,after={key:u.get('pending_mp_change',0) for key,u in self.runtime.units.items()})
        return value
