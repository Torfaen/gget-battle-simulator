"""Original battle-context user records, separate from pilot kill counters."""
import math
from dataclasses import dataclass,field
from .original import f32,i32,mul,round_down
from .trait_factory import i64

RECORD_FIELDS=('super_critical_count','super_high_count','super_charged_count','chance_step_count',
    'map_weapon_count','over_kill_count','kill_count','support_defense_count','support_attack_count',
    'counter_support_count','character_skill_count','supporter_skill_count')


@dataclass
class IngameUserRecords:
    counts: dict=field(default_factory=lambda:dict.fromkeys(RECORD_FIELDS,0))
    max_damage: int=0

    def increment(self,kind,battle_side):
        # Own only. Guest and friendly units do not contribute to this record.
        kind=i32(kind)
        if i32(battle_side)!=0 or not 1<=kind<=12:return False
        key=RECORD_FIELDS[kind-1];self.counts[key]=i64(self.counts[key]+1);return True

    def set_max_damage(self,value,battle_side):
        value=i64(value)
        if i32(battle_side)!=0 or value<=self.max_damage:return False
        self.max_damage=value;return True

    def snapshot(self):return dict(self.counts,max_damage=self.max_damage)


def _ratio(numerator,denominator):
    numerator=f32(numerator);denominator=f32(denominator)
    if denominator==0:
        return math.nan if numerator==0 else math.copysign(math.inf,numerator*math.copysign(1,denominator))
    return f32(numerator/denominator)


def overkill_rate(max_hp,current_hp):
    # Native multiplies in Int32 before converting to float, then truncates.
    return max(0,round_down(_ratio(i32(i32(current_hp)*100),i32(-i32(max_hp)))))


def current_hp_state(max_hp,current_hp):
    """Raw byte states: Normal1, Pinch2, Kill3, OverKill4."""
    maximum=i32(max_hp);current=i32(current_hp)
    if current>0:return 2 if _ratio(mul(current,100),maximum)<30 else 1
    return 4 if overkill_rate(maximum,current)>9 else 3


def entity_is_alive(existence):
    """IngameUnitEntity.IsAlive differs from IsAliveOnMap and HP > 0."""
    offset=(i32(existence)-2)&0xffffffff
    return True if offset>9 else bool((0x10e>>offset)&1)


def unit_side(unit):return unit['battle_side'] if 'battle_side' in unit else unit['formation_group']['side']
def unit_existence(unit):return unit['existence'] if 'existence' in unit else unit['chance_step']['existence']


def record_weapon_strike(records,action,actor,damage_target,result):
    """Original post-consumption branches, before constructing visual results.

    No hit gate. Main attacks examine the actual current damage recipient, after
    immediate trait events; other strike roles do not increment Kill/OverKill.
    """
    if action not in ('main_attack','counter','support_attack','counter_support'):raise ValueError('Unknown strike action')
    events=[]
    def increment(kind):
        side=unit_side(actor);changed=records.increment(kind,side)
        events.append(dict(kind=kind,actor=actor['id'],battle_side=side,changed=changed))
    if action=='support_attack':increment(9)
    elif action=='counter_support':increment(10)
    if i32(result.raw['critical_rate']['kind'])==2:increment(1)
    hp_state=None
    if action=='main_attack':
        hp_state=current_hp_state(damage_target['max_hp'],damage_target['hp'])
        if hp_state==4:increment(6)
        if not entity_is_alive(unit_existence(damage_target)):increment(7)
    return dict(events=events,damage_target_hp_state=hp_state,values=records.snapshot())
