"""Master MAP level definitions and original runtime property corrections.

Weapon level is an explicit captured/caller value. Enhanced custom-core and live
instance overrides require their own importer and are never guessed here.
"""
from copy import deepcopy
from .original import i32,SOURCE_MASTER_SHA256
from .battle_evaluation import HitRate
from .weapon_corrections import sum_field,truncdiv
from .weapon_power_boosts import effective_power


def definition(master,weapon_id,level,*,custom_core_enhance_count=0):
    if master.sha256!=SOURCE_MASTER_SHA256:raise ValueError('MAP definitions require the analyzed master snapshot')
    if custom_core_enhance_count:raise NotImplementedError('Import enhanced MAP custom-core sources explicitly')
    level=int(level)&0xffffffff
    spec=master.index['m_weapon'][weapon_id]
    if spec['MapWeaponRangeTypeIndex'] not in range(1,7):raise ValueError('Original MAP weapon definition required')
    status=master.index['m_weapon_status'][spec['WeaponStatusId']]
    growth=master.index['m_weapon_level_growth_pattern_set'].get(status['WeaponLevelGrowthPatternSetId'],{})
    pattern=status['OverrideWeaponStatusChangePatternSetId'] or growth.get('WeaponStatusChangePatternSetId',0)
    rows=[r for r in master.tables['m_weapon_status_change_pattern'] if r['WeaponStatusChangePatternSetId']==pattern and r['CurrentWeaponLevel']==level]
    if len(rows)!=1:raise ValueError('Missing or ambiguous MAP weapon level pattern')
    change=rows[0]
    trait_pattern=status['OverrideWeaponTraitChangePatternSetId'] or growth.get('WeaponTraitChangePatternSetId',0)
    traits=[deepcopy(master.index['m_weapon_trait'][r['WeaponTraitId']]) for r in master.tables['m_weapon_trait_change_pattern']
            if r['WeaponTraitChangePatternSetId']==trait_pattern and r['CurrentWeaponLevel']==level and r['WeaponTraitId']]
    result=dict(id=weapon_id,main_weapon_id=spec['MainWeaponId'],level=level,map_range_type=spec['MapWeaponRangeTypeIndex'],
        map_trait_category=spec['MapWeaponTraitCategoryTypeIndex'],effect_range=status['MapWeaponEffectRange'],shooting_range=status['MapWeaponShootingRange'],
        attack_attributes=[r['AttackAttributeTypeIndex'] for r in master.tables['m_attack_attribute_set'] if r['AttackAttributeSetId']==spec['AttackAttributeSetId']],
        weapon_attributes=[r['WeaponAttributeTypeIndex'] for r in master.tables['m_weapon_attribute_set'] if r['WeaponAttributeSetId']==spec['WeaponAttributeSetId']],
        weapon_traits=traits,weapon_trait_types=[r['WeaponTraitTypeIndex'] for r in traits],required_tension=spec['TensionTypeIndex'],
        capability=deepcopy(master.index['m_weapon_capability_set'][spec['WeaponCapabilitySetId']]),
        impact_point_max_select_count=status['MapWeaponImpactPointMaxSelectCount'],can_use_after_move=status['MapWeaponCanUseAfterMove'],
        source='master-map-explicit-level-no-custom-core',master_sha256=master.sha256)
    stats={key:truncdiv(i32(status[field]*change[correction]),100) for key,field,correction in
           (('power','Power','PowerCorrectionRate'),('hit','HitRate','HitRateCorrectionRate'),('critical','CriticalRate','CriticalRateCorrectionRate'),('en','En','EnCorrectionRate'))}
    stats.update(hp_recovery_rate=change['HpRecoveryRate'],en_recovery_rate=change['EnRecoveryRate'],mp_recovery_value=change['MpRecoveryValue'],
        ammo_capacity=change['MapWeaponAmmoCapacity'],mp_consumption=change['MpConsumptionValue'],hp_consumption_rate=change['HpConsumptionRate'])
    result['map_level_stats']=stats;result.update(stats)
    if result['map_range_type']==6:
        fixed=[r for r in master.tables['m_weapon_map_fixed_position_parameter'] if r['WeaponId']==weapon_id]
        if len(fixed)!=1:raise ValueError('Missing fixed-position MAP parameters')
        result['fixed_shooting_direction']=fixed[0]['ShootingDirectionIndex']
    return result


def recovery_rate(base,correction,maximum):
    # A zero base short-circuits; corrections cannot create a recovery channel.
    return max(0,min(maximum,i32(base+correction))) if base else 0


def properties(unit,weapon,records):
    base=weapon['map_level_stats'];result=dict(weapon)
    traits={row['WeaponTraitTypeIndex'] for row in weapon.get('weapon_traits',[])}
    fixed=15 in traits;sure=5 in traits
    result['hit_rate_spec']=HitRate(i32(base['hit']+(0 if fixed else sum_field(records,'HitRate',weapon))),sure,fixed)
    result['critical']=i32(base['critical']+sum_field(records,'CriticalRate',weapon))
    # MAP has no distance-boost call here, and base zero bypasses even the clamp.
    result['power']=effective_power(unit,dict(weapon,power=base['power']),records) if base['power'] else 0
    result['hp_recovery_rate']=recovery_rate(base['hp_recovery_rate'],sum_field(records,'HpRecoveryRate',weapon),100) if base['hp_recovery_rate'] else 0
    result['en_recovery_rate']=recovery_rate(base['en_recovery_rate'],sum_field(records,'EnRecoveryRate',weapon),100) if base['en_recovery_rate'] else 0
    result['mp_recovery_value']=recovery_rate(base['mp_recovery_value'],sum_field(records,'MpRecoveryValue',weapon),12) if base['mp_recovery_value'] else None
    return result
