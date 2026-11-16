"""Post-weapon-disable support records and ordinary engagement/use logs.

Selected main weapon and cached initial defender are explicit caller inputs.
Result lists are the actual outer result lists, not selected support roles.
Stage history is an explicit context value; this does not schedule its events.
"""
from copy import copy,deepcopy
from dataclasses import dataclass
from .original import i32,SOURCE_MASTER_SHA256
from .trait_factory import i64
from .mechanics import short
from .battle_user_records import unit_side
from .ordinary_battle_mp import ExchangeStrikeResults


@dataclass(frozen=True)
class StrikeLogResult:
    action: str
    actor: dict
    weapon_id: object

    @classmethod
    def completed(cls,action,actor,result):
        return cls(action,actor,i64(result.raw['attack_weapon'].base['id']))

    def snapshot(self):return dict(action=self.action,actor=self.actor['id'],weapon_id=self.weapon_id)


class ExchangeLogResults(ExchangeStrikeResults):
    pass


def fixed_log_user_ids(runtime):
    if runtime.master.sha256!=SOURCE_MASTER_SHA256:raise ValueError('Pinned unit identity binding required')
    ids={}
    for key,u in runtime.units.items():
        if 'source_npc_id' in u:
            # NpcUserUnitEntity constructs UserUnitId from sign-extended
            # IngameUnitId.ToInt, not the NPC/master-unit placement identity.
            ids[key]=i64(i32(u['native_unit_id']))
        else:ids[key]=i64(int(u['account_configuration']['user_unit_id']))
    return ids


def execute_ordinary_engagement_logs(s,p,results,selected_weapon_id,cached_support_defender):
    if cached_support_defender is not None:s.support_record(p.support_defender_unit)
    if p.attack_side_support_defender_unit is not None:s.support_record(p.attack_side_support_defender_unit)
    s.engagement(p.main_attacker_unit,p.main_target_unit)
    s.engagement(p.main_target_unit,p.main_attacker_unit)
    s.used_weapon(p.main_attacker_unit,selected_weapon_id)
    for group in (results.normal_supports,results.preemptive_supports):
        for result in group:
            s.used_weapon(result.actor,result.weapon_id)
            s.attack_support(result.actor,p.main_attacker_unit)
    for result in (results.normal_counter,results.preemptive_counter):
        if result is not None and result.weapon_id is not None:s.used_weapon(p.main_target_unit,result.weapon_id)
    for group in (results.normal_counter_supports,results.preemptive_counter_supports):
        for result in group:s.used_weapon(result.actor,result.weapon_id)
    if p.support_defender_unit is not None:s.defense_support(p.support_defender_unit,p.main_target_unit)
    if p.attack_side_support_defender_unit is not None:s.defense_support(p.attack_side_support_defender_unit,p.main_attacker_unit)


class FixedStageEngagementLogs:
    def __init__(self,calculation,records,logs,*,user_ids,turn,after_historic_moment):
        self.calculation=calculation;self.records=records;self.logs=logs
        self.user_ids=dict(user_ids);self.turn=i32(turn);self.after_historic_moment=bool(after_historic_moment);self.events=[]

    def execute(self,p,results,*,selected_weapon_id,cached_support_defender):
        units=self.calculation.runtime.units
        roles=(p.main_attacker_unit,p.main_target_unit,p.support_defender_unit,p.attack_side_support_defender_unit,cached_support_defender)
        if any(u is None for u in roles[:2]):raise ValueError('Both main engagement participants required')
        if cached_support_defender is not None and p.support_defender_unit is None:
            raise ValueError('Cached defender gate requires current defender')
        for result in results.all_results():
            if not isinstance(result,StrikeLogResult):raise ValueError('Original outer log result projection required')
        for u in (*roles,*(r.actor for r in results.all_results())):
            if u is not None and units.get(u['id']) is not u:raise ValueError('Canonical engagement units required')
        # Preflight the complete boundary; successful native ordering is kept.
        trial=copy(self);trial.records,trial.logs=deepcopy((self.records,self.logs));trial.events=[]
        trial._execute(p,results,selected_weapon_id,cached_support_defender)
        self.events=[];self._execute(p,results,selected_weapon_id,cached_support_defender)
        return dict(events=deepcopy(self.events),logs=self.logs.snapshot(),user_records=self.records.snapshot())

    def _execute(self,p,results,weapon,cached):execute_ordinary_engagement_logs(self,p,results,weapon,cached)
    def user_id(self,u):return i64(self.user_ids[u['id']])
    def support_record(self,u):
        side=i32(unit_side(u));self.records.increment(8,side);self.events.append(['record8',u['id'],side])
    def engagement(self,a,t):
        self.logs.add_engagement(self.turn,a['id'],t['id'],self.after_historic_moment)
        self.events.append(['engagement',self.turn,a['id'],t['id'],self.after_historic_moment])
    def used_weapon(self,u,w):
        if w is None:raise ValueError('Support result requires an original weapon')
        user=self.user_id(u);weapon=i64(w);self.logs.add_used_weapon(user,weapon);self.events.append(['weapon',user,weapon])
    def attack_support(self,u,t):
        user=self.user_id(u);target=self.user_id(t)
        self.logs.add_attack_support(user,target);self.events.append(['attack_support',user,target])
    def defense_support(self,u,t):
        user=self.user_id(u);target=self.user_id(t);count=short(u['support_budget']['defense_used'])
        self.logs.add_defense_support(user,target,count);self.events.append(['defense_support',user,target,count])
