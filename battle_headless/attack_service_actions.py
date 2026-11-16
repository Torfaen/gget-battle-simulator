"""Original public AttackService methods over the shared actual repository.

These services apply one precomputed result and record its actor-specific side
effects. Attack selection, support eligibility, debuff grant and the enclosing
exchange/battle callbacks are separate callers.
"""
from copy import deepcopy
from .original import i32
from .trait_factory import i64
from .mechanics import SupportBudget
from .battle_attack_application import FixedStageAttackApplication,attack_by_weapon
from .trait_consumption import consume_guaranteed_chance_step


MAIN_DAMAGE='damage_given_by_attack_and_counter'
SUPPORT_DAMAGE='damage_given_by_support_attack_and_counter_support'


def _record_damage_and_kill(s,actor,killer,target,result,field):
    previous=s.get_damage_given(actor,field)
    s.set_damage_given(actor,field,i64(previous+result.hit_hp_total_damage))
    # This is the stored calculator flag, not a fresh target.IsBroken check.
    if result.raw['attack_target_is_broken'] and not s.can_escape(target):
        s.kill(killer,target)


def main_attack(s,main_attacker,main_target,damage_target,result):
    attack_by_weapon(s,main_attacker,damage_target,result)
    root=s.map_root()
    coord=s.find_origin(root,s.unit_id(main_target),True)
    if coord is None:raise ValueError('Original main-target origin has no value')
    s.set_last_target_coord(main_attacker,coord)
    s.set_last_main_target(main_attacker,s.unit_id(main_target))
    _record_damage_and_kill(s,main_attacker,main_attacker,damage_target,result,MAIN_DAMAGE)


def counter(s,main_target,damage_target,result):
    attack_by_weapon(s,main_target,damage_target,result)
    _record_damage_and_kill(s,main_target,main_target,damage_target,result,MAIN_DAMAGE)


def _support(s,actor,killer,target,result):
    attack_by_weapon(s,actor,target,result)
    if s.use_support_attack_and_counter(actor)==16:
        s.consume_one_time_support(s.unit_id(actor))
    _record_damage_and_kill(s,actor,killer,target,result,SUPPORT_DAMAGE)


def support_attack(s,main_attacker,support_attacker,target,result):
    _support(s,support_attacker,main_attacker,target,result)


def counter_support(s,counter_support_unit,damage_target,main_target,result):
    _support(s,counter_support_unit,main_target,damage_target,result)


def support_budget_after_use(unit):
    """Read authoritative correction limits, then the mutable Int16 counters."""
    budget=SupportBudget(**unit['support_budget']);c=unit['scalar_corrections']
    budget.attack_limit=i32(c.get('SupportAttackAndCounterLimit',0))
    budget.attack_adaptive_limit=i32(c.get('SupportAttackAndCounterAdaptiveLimit',0))
    timing=budget.consume('attack')
    return budget,timing


class FixedStageAttackActions(FixedStageAttackApplication):
    def __init__(self,calculation,*,find_origin):
        super().__init__(calculation);self._find_origin=find_origin

    def _run(self,action,args,actor,target,result,*,main=None,support=False):
        self.preflight(actor,target,result)
        participants=[]
        for unit in (actor,target,main):
            if unit is None:continue
            if self.runtime.units.get(unit['id']) is not unit:raise ValueError('Canonical shared repository participants required')
            if unit.get('prediction'):raise NotImplementedError('Actual entities required')
            if not any(u is unit for u in participants):participants.append(unit)
        # Validate unresolved inputs before resource use; this binding does not
        # reproduce partial writes on native exceptional returns.
        if action is main_attack:
            if self._find_origin(self.calculation.map,main['id'],True) is None:
                raise ValueError('Original main-target origin has no value')
        if support:
            _,timing=support_budget_after_use(actor)
            if timing==16:
                trial_units=deepcopy(self.runtime.units);trial_groups=deepcopy(self.runtime.groups)
                consume_guaranteed_chance_step(trial_groups,trial_units.values(),self.calculation.phase,actor['id'],16)
        self.events=[];before={u['id']:deepcopy(u) for u in participants}
        action(self,*args)
        return dict(action=action.__name__,events=deepcopy(self.events),before=before,
            after={u['id']:deepcopy(u) for u in participants})

    def main_attack(self,main_attacker,main_target,damage_target,result):
        return self._run(main_attack,(main_attacker,main_target,damage_target,result),main_attacker,damage_target,result,main=main_target)
    def counter(self,main_target,damage_target,result):
        return self._run(counter,(main_target,damage_target,result),main_target,damage_target,result)
    def support_attack(self,main_attacker,support_attacker,target,result):
        return self._run(support_attack,(main_attacker,support_attacker,target,result),support_attacker,target,result,main=main_attacker,support=True)
    def counter_support(self,counter_support_unit,damage_target,main_target,result):
        return self._run(counter_support,(counter_support_unit,damage_target,main_target,result),counter_support_unit,damage_target,result,main=main_target,support=True)

    def map_root(self):return self.calculation.map
    def unit_id(self,unit):return unit['id']
    def find_origin(self,root,identity,include_reserve):
        value=self._find_origin(root,identity,include_reserve)
        self.events.append(dict(event='find_origin',unit=identity,include_reserve=include_reserve,coord=deepcopy(value)))
        return value
    def set_last_target_coord(self,unit,coord):
        unit['last_targeted_unit_coord']=list(coord)
        self.events.append(dict(event='set_last_target_coord',unit=unit['id'],coord=list(coord)))
    def set_last_main_target(self,unit,identity):
        unit['last_main_attacked_unit_id']=identity
        self.events.append(dict(event='set_last_main_target',unit=unit['id'],target=identity))
    def get_damage_given(self,unit,field):return i64(unit.get(field,0))
    def set_damage_given(self,unit,field,value):
        unit[field]=i64(value)
        self.events.append(dict(event='set_damage_given',unit=unit['id'],field=field,value=unit[field]))
    def kill(self,unit,target):
        # IngameUnitEntity.Kill only increments its UserCharacter counter; the
        # target argument is unused. Defeat existence was written by Damage.
        unit['pilot']['kill_count_in_battle']=i32(unit['pilot'].get('kill_count_in_battle',0)+1)
        self.events.append(dict(event='kill',unit=unit['id'],target=target['id'],count=unit['pilot']['kill_count_in_battle']))
    def use_support_attack_and_counter(self,unit):
        budget,timing=support_budget_after_use(unit)
        for key in ('attack_used','attack_adaptive_used','attack_once','total_attack_used'):
            unit['support_budget'][key]=getattr(budget,key)
        self.events.append(dict(event='use_support_attack_and_counter',unit=unit['id'],timing=timing))
        return timing
    def consume_one_time_support(self,identity):
        result=consume_guaranteed_chance_step(self.runtime.groups,self.runtime.units.values(),self.calculation.phase,identity,16)
        self.events.append(dict(event='consume_one_time_support',unit=identity,result=result))
        return result
