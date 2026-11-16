"""Version-pinned battle score arithmetic and accumulated score state.

Initial HP/counts and enemy battle powers are explicit original preparation
snapshots. They must not be reconstructed from current damaged/buffed units.
"""
import math
from dataclasses import dataclass,field,asdict
from .original import i32,f32,mul,round_down,round_up
from .trait_factory import i64


def _div_ieee(a,b):
    if b:return a/b
    if not a:return math.nan
    return math.copysign(math.inf,math.copysign(1,a)*math.copysign(1,b))


def _trunc_div(value,divisor):return (abs(value)//divisor)*(-1 if value<0 else 1)


def _double_to_long(value):
    # FCVTZS X followed by the client's explicit positive-infinity override.
    if math.isnan(value):return 0
    if value==math.inf:return -2**63
    return max(-2**63,min(2**63-1,int(value))) if math.isfinite(value) else -2**63


def overkill_rate(max_hp,current_hp):
    numerator=f32(i32(i32(current_hp)*100));denominator=f32(i32(-i32(max_hp)))
    return max(0,round_down(f32(_div_ieee(numerator,denominator))))


@dataclass
class BattleScoreCalculator:
    base_score: int
    own_total_unit_count: int
    own_total_initial_hp: int
    enemy_total_unit_count: int
    enemy_total_battle_power: int
    stage_clear_weight: int
    overkill_weight: int
    survivor_weight: int
    kill_weight: int
    damage_taken_weight: int
    overkill_bonus_rates: dict
    survival_bonus_rates: dict
    damage_taken_bonus_rates: dict
    enemy_battle_power: dict

    def __post_init__(self):
        for name in ('base_score','own_total_initial_hp','enemy_total_battle_power'):
            setattr(self,name,i64(getattr(self,name)))
        for name in ('own_total_unit_count','enemy_total_unit_count','stage_clear_weight','overkill_weight',
                     'survivor_weight','kill_weight','damage_taken_weight'):
            setattr(self,name,i32(getattr(self,name)))
        # Original ctor copies these into SortedDictionary with BonusRateComparer
        # (y.CompareTo(x)). The other two dictionaries are retained by reference.
        for name in ('overkill_bonus_rates','damage_taken_bonus_rates'):
            setattr(self,name,dict(sorted(getattr(self,name).items(),reverse=True)))

    def stage_clear_point(self):return _trunc_div(i64(self.base_score*self.stage_clear_weight),100)

    def kill_point(self,identity):
        total=self.enemy_total_battle_power
        if total==0:return 0
        ratio=float(i32(self.enemy_battle_power.get(identity,0)))/float(total)
        return _double_to_long((ratio*float(i64(self.base_score*self.kill_weight)))/100.0)

    def overkill_point(self,identity,rate):
        total=self.enemy_total_battle_power
        if total==0:return 0
        ratio=float(i32(self.enemy_battle_power.get(identity,0)))/float(total)
        for threshold,bonus in self.overkill_bonus_rates.items():
            if threshold<=i32(rate):
                product=i64(i64(self.base_score*i32(bonus))*self.overkill_weight)
                return _double_to_long((ratio*float(product))/10000.0)
        return 0

    def survival_point(self,count):
        # The original gate is initial HP, not initial unit count.
        if self.own_total_initial_hp==0:return 0
        bonus=i32(self.survival_bonus_rates.get(i32(count),0))
        return _trunc_div(i64(i64(self.base_score*bonus)*self.survivor_weight),10000)

    def damage_taken_point(self,current_hp):
        total=self.own_total_initial_hp
        if total==0:return 0,100
        if total<0:raise ValueError('Original HP clamp requires nonnegative initial total HP')
        hp=max(0,min(total,i64(current_hp)))
        remaining=round_up(f32(_div_ieee(mul(f32(hp),100),f32(total))))
        rate=max(0,min(100,i32(100-remaining)))
        for threshold,bonus in self.damage_taken_bonus_rates.items():
            if rate>=threshold:
                return _trunc_div(i64(i64(self.base_score*i32(bonus))*self.damage_taken_weight),10000),rate
        return 0,rate


@dataclass
class BattleScore:
    kill_points: dict=field(default_factory=dict)
    overkill_points: dict=field(default_factory=dict)
    killed_points: dict=field(default_factory=dict)
    overkilled_points: dict=field(default_factory=dict)
    stage_clear_point: int=0
    damage_taken_point: int=0
    survival_point: int=0
    kill_count: int=0
    survival_count: int=0
    damage_taken_rate: int=0

    def add_kill_point(self,attacker,killed,point):
        self.kill_points[attacker]=i64(self.kill_points.get(attacker,0)+point)
        self.killed_points[killed]=i64(self.killed_points.get(killed,0)+point)
        self.kill_count=i32(self.kill_count+1)

    def add_overkill_point(self,attacker,killed,point):
        self.overkill_points[attacker]=i64(self.overkill_points.get(attacker,0)+point)
        self.overkilled_points[killed]=i64(self.overkilled_points.get(killed,0)+point)

    def total(self):
        return i64(sum(self.kill_points.values())+sum(self.overkill_points.values())+
            self.stage_clear_point+self.damage_taken_point+self.survival_point)

    def snapshot(self):return dict(asdict(self),total=self.total())
