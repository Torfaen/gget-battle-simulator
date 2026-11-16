"""Version-pinned combat primitives recovered from the original IL2CPP methods."""
from copy import deepcopy
from .original import i32, round_down, round_up, div, mul
from .mechanics import predicted_hp_damage


def character_attack_value(pilot, attributes):
    """IngameUnitEntity.GetBuffedCharacterAttackValue, 0x613e4b0.

    Multiple attack attributes choose the maximum applicable stat, including a
    zero initial accumulator. They are neither summed nor averaged.
    """
    names={1:'Ranged',2:'Melee',3:'Awaken'}
    return max([0]+[pilot[names[a]] if a in names else 0 for a in attributes])


def initial_mp(correction=0):
    """UserCharacterEntity.GetInitialMp, 0x6191f88."""
    return min(12,max(0,i32(correction)))


def resource_percent(maximum,rate):
    """CalcHp/En/MpByMax*Percent: original int32 product, float32, round up."""
    return round_up(div(i32(maximum*rate),100))


def same_group(left,right):
    """IngameTraitTarget null checks + IngameUnitGroup.IsSameGroup, 0x5fce2a4."""
    return bool(left is not None and right is not None and left['link_type']!=0
        and right['link_type']!=0 and left['link_type']==right['link_type']
        and left['side']==right['side'] and left['id']==right['id'])


def valid_tension(current, required):
    """TensionTypeExtensions.IsValidTension, 0x66e556c."""
    if not 0<=required<=5:raise ValueError('Invalid original required tension')
    return required==0 or current>=required


def battle_mp_delta(change, fluctuation_rate=0, attack_failure=False, anti_failure_reduction=False):
    """AddMpChangeValueForBattleAction/OnAttackFailure, 0x6142240/0x61422a8."""
    if attack_failure and anti_failure_reduction:return 0
    return i32(change+round_down(div(mul(change,fluctuation_rate),100)))


def normal_counter_policy(hp, barrier, total_damage, hp_damage, defended_total_damage,
                          defended_hp_damage, *, can_defend=True, support_defense_enabled=False,
                          preemptive_available=False, manual_side=False, action_inability=False):
    """Dispatch policy in NormalAICounterRuleBehavior.Decide (0x5cffa84).

    This chooses an original service branch. Its return value is NOT a selected
    weapon: weapon prediction/selection is a distinct service and must execute
    before a counter attack can be scheduled.
    """
    if action_inability:return 'action_inability'
    if support_defense_enabled:total_damage=hp_damage=0
    remaining=i32(hp-predicted_hp_damage(total_damage,hp_damage,barrier))
    if remaining>=1:return 'counter_service'
    if preemptive_available:return 'preemptive_service'
    if support_defense_enabled:defended_total_damage=defended_hp_damage=0
    defended=predicted_hp_damage(defended_total_damage,defended_hp_damage,barrier)
    if can_defend and hp>defended:return 'defend_service'
    return 'evade_service' if manual_side else 'counter_service'


# Exact properties replaced by per-property maxima in TraitCorrections._SumGroup.
# Other supported scalar properties retain additive composition across groups.
MAX_GROUP_FIELDS=frozenset(('MaxHpRate','MaxHpValue','MaxEnRate','MaxEnValue','InitialMp',
    'MpFluctuationRate','AttackRate','AttackValue','DefenseRate','DefenseValue',
    'MobilityRate','MobilityValue','MovementDistanceValue','EvasionRate',
    'DamageGivenCorrectionRate','DamageGivenCorrectionValue','DamageNegationValue'))
SCALAR_FIELDS=MAX_GROUP_FIELDS|frozenset(('AbsoluteEvasionCount','ActionInabilityCount',
    'AntiAttackEvadedMpReductionCount','AntiMpChangeCount','AttackInabilityCount',
    'AttackTargetDefenseReductionRate','AttackTargetMobilityReductionRate','ChanceStepMaxCount',
    'CharacterAwakenBuffRate','CharacterAwakenBuffValue','CharacterAwakenRate','CharacterAwakenValue',
    'CharacterDefenseRate','CharacterDefenseValue','CharacterMeleeRate','CharacterMeleeValue',
    'CharacterRangedRate','CharacterRangedValue','CharacterReactionRate','CharacterReactionValue',
    'CriticalDamageGivenCorrectionRate','CriticalDamageGivenCorrectionValue','GuaranteedChanceStepCount',
    'GuaranteedChanceStepWeaponUsedOnlyCount','GuaranteedCriticalCount','MovementInabilityCount',
    'MpLimitBreakCount','ParryRate','PostAttackMoveCount','PostMapWeaponAttackMoveCount',
    'SupportAttackAndCounterAdaptiveLimit','SupportAttackAndCounterLimit','SupportDefendDistanceValue',
    'SupportDefenseAdaptiveLimit','SupportDefenseExtendToCounter','SupportDefenseLimit',
    'SureHitAttackCount','UnbreakableCount'))


class ScalarCorrections:
    """Scalar subset of TraitCorrections; complex object/list fields stay external.

    AddWithMaxValueGroup replaces the existing entry for its key. _SumGroup then
    takes maxima across all surviving group entries for exactly 17 properties.
    The zero seed matters: an all-negative max-group property contributes zero.
    """
    def __init__(self):
        self.normal={}
        self.groups={}

    def add(self, values):
        if values.keys()-SCALAR_FIELDS:raise NotImplementedError('Non-scalar or unknown trait correction')
        for field,value in values.items():
            if type(value) is not int:raise TypeError('Only original scalar corrections are accepted')
            self.normal[field]=i32(self.normal.get(field,0)+value)

    def replace_group(self, key, values):
        if values is None:return
        if values.keys()-SCALAR_FIELDS:raise NotImplementedError('Non-scalar or unknown trait correction')
        if any(type(v) is not int for v in values.values()):raise TypeError('Scalar corrections required')
        self.groups[key]=deepcopy(values)

    def total(self):
        aggregate={}
        for values in self.groups.values():
            for field,value in values.items():
                aggregate[field]=(max(aggregate.get(field,0),value) if field in MAX_GROUP_FIELDS
                                  else i32(aggregate.get(field,0)+value))
        for field,value in self.normal.items():aggregate[field]=i32(aggregate.get(field,0)+value)
        return aggregate
