"""Original prediction/actual evaluation gates, client 2.7.0.

These consume evaluated hit/critical/parry rates, not raw master weapon rates.
"""
from dataclasses import dataclass
from .original import i32,mul,round_down


@dataclass(frozen=True)
class HitRate:
    pure_value: int
    sure_hit: bool=False
    fixed: bool=False

    def __post_init__(self):object.__setattr__(self,'pure_value',max(0,i32(self.pure_value)))

    @property
    def value(self):return 100 if self.sure_hit else self.pure_value


def compose_hit_rate(base,*,target_action_inability=False,absolute_evasion=False,
                     attacker_sure_hit=False,mobility_correction=0,ability_correction=0,
                     map_effect=0,counter_type=0):
    """HitRateCalculateService._CalcHitRate 0x6212188, evaluated-input subset."""
    if target_action_inability:return HitRate(100)
    if base.sure_hit:return base
    if absolute_evasion:return HitRate(0,False,base.fixed)
    if base.fixed:return HitRate(min(100,base.pure_value),False,True)
    if attacker_sure_hit:return HitRate(100,True)
    total=i32(i32(i32(mobility_correction+ability_correction)+map_effect)+base.pure_value)
    value=round_down(mul(total,0.5 if counter_type==2 else 1))
    return HitRate(max(10,min(100,value)))


def evaluate_hit(rate,rng,*,sure_hit=False,target_is_broken=False,prediction=False):
    """BattleCalculateService._EvaluateHit, RVA 0x6209124."""
    if sure_hit:return True
    if prediction:return i32(rate)>0
    if target_is_broken:return True
    return rng.evaluate(i32(rate))


def evaluate_critical(rate,tension,rng,*,guaranteed=False,prediction=False):
    """_EvaluateCritical 0x620b914; returns Critical_NONE/NORMAL/SUPER."""
    if i32(rate)>=100 or guaranteed:
        enabled=True
    elif prediction:
        enabled=False
    else:
        enabled=rng.evaluate(i32(rate))
    return (2 if tension==5 else 1) if enabled else 0


def evaluate_parry(rate,rng,*,prediction=False):
    """_EvaluateParry 0x620926c; the caller must apply eligibility gates."""
    if prediction or i32(rate)<1:return False
    return rng.evaluate(i32(rate))
