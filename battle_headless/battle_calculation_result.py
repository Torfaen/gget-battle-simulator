"""Original result storage and hit-dependent views, separate from damage writes."""
from dataclasses import dataclass
from .original import i32


@dataclass(frozen=True)
class HitResult:
    kind: int = 0
    hit_rate: object = None

    @classmethod
    def create(cls,is_hit,is_parry,hit_rate):return cls(2 if is_parry else 1 if is_hit else 3,hit_rate)
    @property
    def is_hit(self):return self.kind==1
    @property
    def is_parry(self):return self.kind==2
    @property
    def is_evaded(self):return self.kind==3


INTEGER_FIELDS=('hp_damage','hp_barrier_damage','en_damage','mp_damage','attack_weapon_power',
    'attack_weapon_en_consumption','attack_target_counter_selection_type','correction_damage_rate')
OPTIONAL_FIELDS=('drained_hp','drained_en','attack_weapon_mp_consumption','attack_weapon_hp_consumption')
BOOLEAN_FIELDS=('attack_target_is_broken','is_support_defend','has_damage_cut')
REFERENCE_FIELDS=('attack_weapon','hit_result','critical_rate','passive_weapon_trait_ids','debuff_weapon_trait_ids')
NORMAL_FIELDS=frozenset(INTEGER_FIELDS+OPTIONAL_FIELDS+BOOLEAN_FIELDS+REFERENCE_FIELDS)


class BattleCalculationResult:
    def __init__(self,**fields):
        if set(fields)!=NORMAL_FIELDS:raise ValueError('Complete original result fields required')
        self.raw=dict(fields)
        for key in INTEGER_FIELDS:self.raw[key]=i32(fields[key])
        for key in OPTIONAL_FIELDS:self.raw[key]=None if fields[key] is None else i32(fields[key])
        for key in BOOLEAN_FIELDS:self.raw[key]=bool(fields[key])
        self.is_action_inability=False;self.is_attack_inability=False

    @classmethod
    def inability(cls,weapon,is_action_inability,is_attack_inability):
        fields={key:0 for key in INTEGER_FIELDS}
        fields.update({key:None for key in OPTIONAL_FIELDS+REFERENCE_FIELDS})
        fields.update({key:False for key in BOOLEAN_FIELDS})
        fields.update(attack_weapon=weapon,attack_weapon_mp_consumption=0,attack_weapon_hp_consumption=0,
            hit_result=HitResult(),critical_rate=dict(value=0,kind=0))
        result=cls(**fields)
        result.is_action_inability=bool(is_action_inability);result.is_attack_inability=bool(is_attack_inability)
        return result

    @property
    def is_hit(self):return self.raw['hit_result'].is_hit
    @property
    def is_parry(self):return self.raw['hit_result'].is_parry
    @property
    def is_evaded(self):return self.raw['hit_result'].is_evaded
    @property
    def hit_rate(self):return self.raw['hit_result'].hit_rate
    @property
    def hp_total_damage(self):return i32(self.raw['hp_damage']+self.raw['hp_barrier_damage'])
    @property
    def hit_hp_damage(self):return self.raw['hp_damage'] if self.is_hit else 0
    @property
    def hit_hp_barrier_damage(self):return self.raw['hp_barrier_damage'] if self.is_hit else 0
    @property
    def hit_hp_total_damage(self):return i32(self.hit_hp_damage+self.hit_hp_barrier_damage)
    @property
    def hit_en_damage(self):return self.raw['en_damage'] if self.is_hit and self.raw['en_damage']>0 else None
    @property
    def hit_mp_damage(self):return self.raw['mp_damage'] if self.is_hit and self.raw['mp_damage']>0 else None
    @property
    def drained_hp(self):return self.raw['drained_hp'] if self.is_hit else None
    @property
    def drained_en(self):return self.raw['drained_en'] if self.is_hit else None


def result_snapshot(result):
    raw=dict(result.raw);hit=raw['hit_result']
    raw['hit_result']=dict(kind=hit.kind,hit_rate=hit.hit_rate)
    return dict(raw=raw,is_action_inability=result.is_action_inability,is_attack_inability=result.is_attack_inability,
        **{name:getattr(result,name) for name in ('hp_total_damage','hit_hp_total_damage','hit_hp_damage',
            'hit_hp_barrier_damage','hit_en_damage','hit_mp_damage','drained_hp','drained_en','is_hit','is_parry','is_evaded','hit_rate')})
