"""Original AI prediction comparator; prediction generation remains external.

Client 2.7.0 AIAttackPredictionSelectRule._CompareCostPerformanceTo,
RVA 0x671e4cc. Inputs are original BattleCalculateResultDto values, not rolls.
"""
from dataclasses import dataclass
import math
from .original import f32, i32, add, mul


@dataclass(frozen=True)
class AttackPrediction:
    hp_total_damage: int
    target_before_total_hp: int
    hit_rate: int
    en_consumption: int
    sort_order: int
    is_danger: bool = False


def cost_performance(prediction):
    damage = min(i32(prediction.hp_total_damage), i32(prediction.target_before_total_hp))
    numerator = f32(i32(i32(prediction.hit_rate) * damage))
    denominator = add(i32(prediction.en_consumption), 1)
    # ARM FDIV follows IEEE 754, including zero EN+1 in synthetic edge cases.
    if denominator == 0:
        ratio = math.nan if numerator == 0 else math.copysign(math.inf, numerator * math.copysign(1, denominator))
    else:
        ratio = f32(numerator / denominator)
    return mul(mul(ratio, i32(prediction.hit_rate)), damage)


def compare_predictions(first, second):
    """Negative puts first ahead: safe, higher score, then lower signed short order.

    Equal priorities deliberately return zero: original list sorting stability
    and prediction-service eligibility are separate contracts.
    """
    if bool(first.is_danger) != bool(second.is_danger):
        return 1 if first.is_danger else -1
    a, b = cost_performance(first), cost_performance(second)
    # System.Single.CompareTo on second's score with first's score as argument.
    if b < a: return -1
    if b > a: return 1
    if b != a:
        if not math.isnan(b): return 1
        if not math.isnan(a): return -1
    short = lambda value: (int(value) + 32768) % 65536 - 32768
    return short(first.sort_order) - short(second.sort_order)
