"""Ordinary movement consumption, four score calls, bonus save and kill logs.

This boundary requires an explicitly prepared score calculator. The service's
initialization method takes explicit initialization-time getters. The supplied
step coordinates are original initial/free-move coordinates, not current unit
position or a legacy moved flag. Fixed-stage story bonus is restricted to type 8.
"""
from copy import copy,deepcopy
from dataclasses import dataclass
from .original import i32
from .battle_score import BattleScore,overkill_rate
from .battle_sides import is_ally_or_friendly
from .battle_user_records import entity_is_alive,unit_existence,unit_side
from .battle_kill_logs import FixedStageKillLogs


@dataclass(frozen=True)
class OrdinaryStepActivity:
    initial_coord: tuple
    free_move_coord: tuple

    def __post_init__(self):
        for name in ('initial_coord','free_move_coord'):
            coord=getattr(self,name)
            if len(coord)!=2:raise ValueError('Original two-component step coordinates required')
            object.__setattr__(self,name,tuple(i32(v) for v in coord))

    def is_moving(self):
        return self.initial_coord[0]!=self.free_move_coord[0] or self.initial_coord[1]!=self.free_move_coord[1]


class BattleScoreService:
    """Original kill-score gates and repository state; initialization is explicit."""
    def __init__(self,calculation,calculator,*,initialized,score=None):
        self.calculation=calculation;self.calculator=calculator
        self.initialized=bool(initialized);self.score=score;self.saves=0

    def initialize_calculator(self,units,configuration,*,battle_power,initial_max_hp):
        # The original returns on repository presence even when initialized is
        # still false. Do not silently repair or re-snapshot that state.
        if self.calculator is not None:return
        from .score_preparation import prepare_calculator
        calculator=prepare_calculator(units,configuration,battle_power=battle_power,initial_max_hp=initial_max_hp)
        self.calculator=calculator;self.initialized=True

    def get_current_score(self):
        # An uninitialized service ignores a pre-existing repository value.
        # The fresh empty object is not saved by this getter.
        return self.score if self.initialized and self.score is not None else BattleScore()

    def add_kill_point(self,attacker,target):
        if target is None or not self.initialized:return
        if attacker is None:raise ValueError('Original score attacker required')
        if not is_ally_or_friendly(unit_side(attacker),0):return
        if entity_is_alive(unit_existence(target)):return
        if self.calculator is None:raise ValueError('Original prepared score calculator required')
        score=self.get_current_score();score.total()
        kill=self.calculator.kill_point(target['id'])
        rate=overkill_rate(self.calculation.max_hp(target),self.calculation.current_hp(target))
        over=self.calculator.overkill_point(target['id'],rate)
        score.add_kill_point(attacker['id'],target['id'],kill)
        score.add_overkill_point(attacker['id'],target['id'],over)
        score.total();self.score=score;self.saves+=1


@dataclass
class StoryBonusRepository:
    value: object=None
    saves: int=0

    def save(self,value):self.value=value;self.saves+=1


def execute_ordinary_movement_score(services,p):
    if services.is_moving():services.consume_movement(p.main_attacker_unit)
    services.add_score(p.main_attacker_unit,p.main_target_unit)
    services.add_score(p.main_attacker_unit,p.support_defender_unit)
    services.add_score(p.main_target_unit,p.main_attacker_unit)
    services.add_score(p.main_target_unit,p.attack_side_support_defender_unit)
    services.fetch_story_bonus()
    services.add_kill_logs(p)


class FixedStageMovementScore:
    def __init__(self,calculation,activity,score_service,kill_logs,story_bonus,*,stage_type):
        if i32(stage_type)!=8:raise ValueError('Only fixed-stage type 8 story bonus is implemented')
        self.calculation=calculation;self.activity=activity;self.score_service=score_service
        self.kill_logs=kill_logs;self.story_bonus=story_bonus;self.events=[];self.movement=None;self.log_result=None

    def execute(self,p):
        if not isinstance(self.activity,OrdinaryStepActivity):raise ValueError('Explicit original step activity required')
        runtime=self.calculation.runtime
        if self.score_service.calculation is not self.calculation or self.kill_logs.runtime is not runtime:
            raise ValueError('Shared score, kill-log and calculation repositories required')
        roles=(p.main_attacker_unit,p.main_target_unit,p.support_defender_unit,p.attack_side_support_defender_unit)
        if any(u is None for u in roles[:2]):raise ValueError('Both main score participants required')
        for u in (*roles,*p.attack_side_units,*p.counter_side_units):
            if u is not None and runtime.units.get(u['id']) is not u:raise ValueError('Canonical movement/score units required')
        # Preflight the entire boundary before movement consumes a trait. This
        # preserves successful behavior, not native partial mutation on errors.
        units,groups,tp,score,logs,bonus=deepcopy((runtime.units,runtime.groups,p,self.score_service.score,
            self.kill_logs.logs,self.story_bonus))
        tr=copy(runtime);tr.units=units;tr.groups=groups
        tc=copy(self.calculation);tc.runtime=tr
        ts=copy(self.score_service);ts.calculation=tc;ts.score=score
        trial=FixedStageMovementScore(tc,self.activity,ts,FixedStageKillLogs(tr,logs,self.kill_logs.step_key),bonus,stage_type=8)
        trial._execute(tp)
        return self._execute(p)

    def _execute(self,p):
        self.events=[];self.movement=None;self.log_result=None
        execute_ordinary_movement_score(self,p)
        return dict(events=deepcopy(self.events),movement=deepcopy(self.movement),
            score=None if self.score_service.score is None else self.score_service.score.snapshot(),
            score_saves=self.score_service.saves,story_bonus=self.story_bonus.value,story_bonus_saves=self.story_bonus.saves,
            kill_logs=self.log_result)

    def is_moving(self):
        result=self.activity.is_moving();self.events.append(['is_moving',result]);return result

    def consume_movement(self,unit):
        self.events.append(['consume_movement',unit['id']])
        self.movement=self.calculation.runtime.consume_battle_end_action(self.calculation.phase,unit,8)

    def add_score(self,attacker,target):
        self.events.append(['add_score',attacker['id'],None if target is None else target['id']])
        self.score_service.add_kill_point(attacker,target)

    def fetch_story_bonus(self):
        self.events.append(['save_story_bonus',0]);self.story_bonus.save(0)

    def add_kill_logs(self,p):
        self.events.append(['kill_logs']);self.log_result=self.kill_logs.execute(p)
