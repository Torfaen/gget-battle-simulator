"""Shared ordinary strike application and AttackUseCase's immediate trait tail.

The caller selects an eligible strike and supplies the original participation,
coordinates and counter reaction. Preemption, cancellation,
view DTOs and battle-end/MP processing remain outside this boundary.
"""
from copy import copy,deepcopy
from dataclasses import dataclass,field
from .attack_service_actions import FixedStageAttackActions
from .weapon_debuff_grants import FixedStageWeaponDebuffGrants
from .weapon_passive_lifecycle import FixedStageWeaponPassiveRuntime
from .trait_consumption import consume_guaranteed_chance_step
from .battle_user_records import IngameUserRecords,record_weapon_strike
from .ordinary_battle_mp import StrikeMpResult


@dataclass
class StrikeBookkeeping:
    # Preserve insertion diagnostics. Native later HashSet traversal requires
    # its own lifecycle evidence; these lists do not establish that ordering.
    player1_weapon_hit: dict=field(default_factory=dict)
    player2_weapon_hit: dict=field(default_factory=dict)
    player1_damaged: dict=field(default_factory=dict)
    player2_damaged: dict=field(default_factory=dict)
    player1_evaded: bool=False
    player2_evaded: bool=False

    def add(self,field_name,identity):getattr(self,field_name).setdefault(identity,None)
    def snapshot(self):
        return {name:list(value) if isinstance(value,dict) else value for name,value in vars(self).items()}


def post_weapon_strike(services,state,action,actor,damage_target,participation,result):
    """Gameplay tail after the public AttackService action, before user records.

    Native fragments are recorded in weapon_strike_tail_verification.json.
    Parry consumes the protected main participant's timing24, not the guard's.
    """
    if action not in ('main_attack','counter','support_attack','counter_support'):raise ValueError('Unknown strike action')
    side=1 if action in ('main_attack','support_attack') else 2;opposite=3-side
    cost=result.raw['attack_weapon_hp_consumption']
    if cost is not None and cost>0:state.add(f'player{side}_damaged',actor['id'])
    if result.is_hit:
        state.add(f'player{side}_weapon_hit',actor['id'])
        state.add(f'player{opposite}_damaged',damage_target['id'])
        services.grant(actor,damage_target,result)
        services.immediate(actor,12,participation,result)
        services.immediate(damage_target,11,participation,result)
    elif result.is_evaded:setattr(state,f'player{opposite}_evaded',True)
    if result.is_parry:
        protected=participation.main_target_unit if side==1 else participation.main_attacker_unit
        services.consume_parry(protected['id'])
    services.consume_used(actor['id'],6)
    services.consume_used(actor['id'],7)


def strike_roles(action,actor,participation):
    """Resolve already selected canonical roles, without choosing eligibility."""
    p=participation
    if action=='main_attack':
        if actor is not p.main_attacker_unit:raise ValueError('Main attacker mismatch')
        main=actor;target=p.main_target_unit;guard=p.support_defender_unit
    elif action=='counter':
        if actor is not p.main_target_unit:raise ValueError('Counter actor mismatch')
        main=actor;target=p.main_attacker_unit;guard=p.attack_side_support_defender_unit
    elif action=='support_attack':
        if not any(actor is u for u in p.support_attack_units):raise ValueError('Support attacker not selected')
        main=p.main_attacker_unit;target=p.main_target_unit;guard=p.support_defender_unit
    elif action=='counter_support':
        if not any(actor is u for u in p.counter_support_units):raise ValueError('Counter supporter not selected')
        main=p.main_target_unit;target=p.main_attacker_unit;guard=p.attack_side_support_defender_unit
    else:raise ValueError('Unknown strike action')
    if main is None or target is None:raise ValueError('Original main participants required')
    return main,target,guard,guard if guard is not None else target


class FixedStageWeaponStrike:
    """Connect calculation/application/grants/events/consumption on one repository.

    Application preflights the entire immediate tail on a shared-identity copy,
    without rolling RNG. It does not reproduce native exception partial writes.
    find_origin is the caller's read-only original map-origin provider.
    """
    def __init__(self,calculation,*,find_origin,state=None,records=None):
        self.calculation=calculation;self.runtime=calculation.runtime;self.find_origin=find_origin
        self.state=StrikeBookkeeping() if state is None else state;self.events=[]
        # The enclosing stage must pass the same context record across battles.
        self.records=IngameUserRecords() if records is None else records

    def execute(self,action,actor,weapon_id,participation,*,actor_coord,target_coord,counter_type):
        _,target,guard,_=self._validate_roles(action,actor,participation)
        if self.calculation.is_action_inability(actor) or self.calculation.is_attack_inability(actor):
            raise NotImplementedError('Resolve original outer action-inability branch before scheduling this strike')
        result=self.calculation.calculate(actor,actor_coord,weapon_id,target,target_coord,counter_type,guard,participation)
        return self.apply_result(action,actor,participation,result)

    def apply_result(self,action,actor,participation,result):
        if result.is_action_inability or result.is_attack_inability:
            raise NotImplementedError('Original outer inability result handling required')
        self._validate_roles(action,actor,participation)
        # Copy all linked objects together so bound weapon/role identity survives.
        units,groups,trial_result,trial_participation=deepcopy((self.runtime.units,self.runtime.groups,result,participation))
        runtime=copy(self.runtime);runtime.units=units;runtime.groups=groups
        calculation=copy(self.calculation);calculation.runtime=runtime
        calculation.passives=FixedStageWeaponPassiveRuntime(runtime,deepcopy(self.calculation.passives.factory),
            source_name=self.calculation.passives.source_name)
        preview=FixedStageWeaponStrike(calculation,find_origin=self.find_origin,state=deepcopy(self.state),records=deepcopy(self.records))
        preview._apply(action,units[actor['id']],trial_participation,trial_result)
        return self._apply(action,actor,participation,result)

    def _validate_roles(self,action,actor,participation):
        roles=strike_roles(action,actor,participation)
        for unit in (*participation.participants,*roles,actor):
            if unit is not None and self.runtime.units.get(unit['id']) is not unit:
                raise ValueError('Canonical shared participation units required')
        return roles

    def _apply(self,action,actor,participation,result):
        s=self.calculation;main,protected,guard,target=strike_roles(action,actor,participation)
        binding=FixedStageAttackActions(s,find_origin=self.find_origin);self.events=[]
        if action=='main_attack':applied=binding.main_attack(actor,protected,target,result)
        elif action=='counter':applied=binding.counter(actor,target,result)
        elif action=='support_attack':applied=binding.support_attack(main,actor,target,result)
        else:applied=binding.counter_support(actor,target,main,result)
        self.events.append(dict(event='application',action=action,events=applied['events']))
        post_weapon_strike(self,self.state,action,actor,target,participation,result)
        records=record_weapon_strike(self.records,action,actor,target,result)
        from .ordinary_engagement_logs import StrikeLogResult
        return dict(action=action,actor=actor['id'],protected_target=protected['id'],damage_target=target['id'],
            result=result,events=self.events,bookkeeping=self.state.snapshot(),user_records=records,
            mp_result=StrikeMpResult.completed(action,actor,guard,result),
            log_result=StrikeLogResult.completed(action,actor,result))

    def grant(self,actor,target,result):
        s=self.calculation
        service=FixedStageWeaponDebuffGrants(self.runtime,s.passives.factory,s.phase,source_name=s.passives.source_name)
        value=service.grant_from_result(actor,target,result)
        self.events.append(dict(event='grant_debuffs',actor=actor['id'],target=target['id'],result=value))
    def immediate(self,owner,timing,participation,result):
        s=self.calculation
        value=self.runtime.immediate_hit_event(s.context,s.phase,owner,timing,participation,weapon=result.raw['attack_weapon'].base)
        self.events.append(dict(event='immediate_trait',owner=owner['id'],timing=timing,result=value))
    def consume_parry(self,identity):
        value=consume_guaranteed_chance_step(self.runtime.groups,self.runtime.units.values(),self.calculation.phase,identity,24)
        self.events.append(dict(event='consume_parry',owner=identity,timing=24,result=value))
    def consume_used(self,identity,timing):
        value=self.runtime.consume(self.calculation.phase,timing,owner_ids={identity})
        self.events.append(dict(event='consume_weapon_used',owner=identity,timing=timing,result=value))
