"""Original ordinary kill-log boundary over canonical units and cached roles.

The caller supplies the current turn/side/step. This boundary follows kill
score updates and precedes end traits; it does not infer a death from HP.
"""
from copy import deepcopy
from dataclasses import asdict,dataclass
from .original import i32
from .trait_factory import i64
from .battle_user_records import entity_is_alive,unit_existence,unit_side
from .combat_primitives import same_group


@dataclass(frozen=True)
class KillLogStepKey:
    turn_number: int
    battle_side: int
    step_number: int

    def __post_init__(self):
        for name in ('turn_number','battle_side','step_number'):
            object.__setattr__(self,name,i32(getattr(self,name)))


@dataclass(frozen=True)
class KillLog:
    step_key: KillLogStepKey
    attacker_unit_id: object
    attacker_side: object
    killed_unit_id: object
    killed_side: int
    weapon_id: object
    attacker_side_participants: tuple
    is_dead_after_leader: bool

    def snapshot(self):
        result=asdict(self);result['attacker_side_participants']=list(self.attacker_side_participants)
        return result


class KillLogList:
    """Add-only turn buckets; lookup returns the first matching killed ID.

    Buckets retain repository insertion order, rather than sorting turns or
    globally flattening append order. No repeated-death suppression is added.
    """
    def __init__(self,logs=()):
        self.turns={}
        for log in logs:self.add(log)

    def add(self,log):self.turns.setdefault(log.step_key.turn_number,[]).append(log)

    def get_by_unit_id(self,identity):
        return next((log for bucket in self.turns.values() for log in bucket if log.killed_unit_id==identity),None)

    def snapshot(self):
        return [dict(turn_number=turn,logs=[log.snapshot() for log in logs]) for turn,logs in self.turns.items()]


def execute_ordinary_kill_logs(services,p):
    """KillLogService.AddLog(BattleParticipationStatus), 0x62203c4."""
    counter=lambda: None if p.main_target_counter_selection.weapon_id in (None,0) else i64(p.main_target_counter_selection.weapon_id)
    active=lambda: None if p.active_attacker_weapon is None else i64(p.active_attacker_weapon.base['id'])
    # Read caches and weapon only when that role is dead. The active attacker
    # weapon is intentional even if it belongs to a support/counter actor.
    if not services.alive(p.main_attacker_unit):
        services.add(p.main_target_unit,counter(),p.main_attacker_unit,p.counter_side_units)
    if p.support_defender_unit is not None and not services.alive(p.support_defender_unit):
        services.add(p.main_attacker_unit,active(),p.support_defender_unit,p.attack_side_units)
    if p.attack_side_support_defender_unit is not None and not services.alive(p.attack_side_support_defender_unit):
        services.add(p.main_target_unit,counter(),p.attack_side_support_defender_unit,p.counter_side_units)
    if not services.alive(p.main_target_unit):
        services.add(p.main_attacker_unit,active(),p.main_target_unit,p.attack_side_units)


class FixedStageKillLogs:
    def __init__(self,runtime,logs,step_key):
        self.runtime=runtime;self.logs=logs;self.step_key=step_key;self.events=[]

    def execute(self,p):
        roles=(p.main_attacker_unit,p.main_target_unit,p.support_defender_unit,p.attack_side_support_defender_unit)
        if any(u is None for u in roles[:2]):raise ValueError('Both main kill-log participants required')
        for u in (*roles,*p.attack_side_units,*p.counter_side_units):
            if u is not None and self.runtime.units.get(u['id']) is not u:raise ValueError('Canonical kill-log units required')
        if not isinstance(self.step_key,KillLogStepKey):raise ValueError('Explicit original turn/side/step required')
        # Evaluate against a copy first so an unresolved leader flag cannot
        # leave a partially appended log. Original successful ordering remains.
        trial=FixedStageKillLogs(self.runtime,deepcopy(self.logs),self.step_key)
        execute_ordinary_kill_logs(trial,p)
        self.events=[];execute_ordinary_kill_logs(self,p)
        return dict(events=deepcopy(self.events),kill_logs=self.logs.snapshot())

    def alive(self,u):
        self.events.append(['alive',u['id']]);return entity_is_alive(unit_existence(u))

    def is_dead_after_leader(self,killed,attacker_id):
        if killed['is_leader']:return False
        leader=next((u for u in self.runtime.units.values()
            if same_group(u['formation_group'],killed['formation_group']) and u['is_leader']),None)
        if leader is None:return False
        previous=self.logs.get_by_unit_id(leader['id'])
        if previous is None:return False
        return not (previous.step_key==self.step_key and previous.attacker_unit_id==attacker_id)

    def add(self,attacker,weapon_id,killed,participants):
        log=KillLog(self.step_key,attacker['id'],i32(unit_side(attacker)),killed['id'],i32(unit_side(killed)),
            weapon_id,tuple(u['id'] for u in participants),self.is_dead_after_leader(killed,attacker['id']))
        self.logs.add(log);self.events.append(['add',log.snapshot()])
