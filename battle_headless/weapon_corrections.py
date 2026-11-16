"""Original weapon correction collections and battle-time shield selection.

Selection runs against a concrete weapon and participant context, never by
mutating the saved base weapon. Unported contexts retain explicit reasons.
"""
from copy import deepcopy
from .original import i32

WEAPON_FIELDS={1:'HitRate',35:'PowerValue',36:'MpRate',37:'MpValue',95:'HpRate',
               97:'HpRecoveryRate',98:'EnRecoveryRate',99:'MpRecoveryValue',44:'ProximityValue',45:'RemotenessValue',47:'EnRate',48:'EnValue',62:'CriticalRate',
               72:'MaxRangeValue',78:'PowerRate',81:'LowHpBoost',82:'HighHpBoost',
               91:'LowMpBoost',92:'HighMpBoost',112:'LowEnBoost',113:'HighEnBoost',
               100:'AmmoUnlimitedCount',101:'EnFreeCount',116:'PreemptiveAttackAdditionCount',117:'MaxAmmoValue'}
SHIELD_KINDS={14:'ReductionRate',15:'ReductionValue',16:'DefensiveReductionRate'}
COMBAT_KINDS=set(WEAPON_FIELDS)|set(SHIELD_KINDS)
COMBAT_STAT_FIELDS={7:'AttackRate',9:'DefenseRate'}
WEAPON_ENUM={'None':0,'Physical':1,'Beam':2,'Special':3,'Almighty':4}
ATTACK_ENUM={'None':0,'Ranged':1,'Melee':2,'Awaken':3}


def truncdiv(value, divisor):
    return (abs(value)//abs(divisor))*(-1 if (value<0)!=(divisor<0) else 1)


def matches_weapon(condition, weapon):
    if condition is None:return True
    for key,field in [('WeaponAttributeTypes','weapon_attributes'),('AttackAttributeTypes','attack_attributes')]:
        required={int(t) for t in condition[key].split(',')} if condition[key] else set()
        if required and 0 not in required and not required.intersection(weapon.get(field,[])):return False
    return True


def sum_field(records,field,weapon):
    value=0
    for record in records:
        if matches_weapon(record.get('condition'),weapon):value=i32(value+record.get(field,0))
    return value


def percentage_delta(records,rate_field,value_field,base,weapon):
    # TraitWeaponCorrections.GetPower/GetEn/GetMp use integer /100, unlike
    # UserUnitStatus's float32 RoundDown path. In particular -5.4 becomes -5.
    return i32(truncdiv(i32(sum_field(records,rate_field,weapon)*base),100)+sum_field(records,value_field,weapon))


def en_consumption(base,records,weapon):
    if sum_field(records,'EnFreeCount',weapon)>0:return 0
    return max(0,min(9999,i32(base+percentage_delta(records,'EnRate','EnValue',base,weapon))))


def normal_max_range(minimum, base_maximum, correction):
    """UnitWeapon.GetRangeWithCustomCore normal AroundRange branch, 0x61ce698."""
    if not 0 <= minimum < 100:
        raise ValueError('Native normal-range minimum must be in 0..99')
    maximum = i32(base_maximum + correction)
    return minimum if maximum < minimum else min(99, maximum)


def _nullable_cost(base,delta,limit):
    # UnitWeapon.GetMpConsumption/GetHpConsumptionRate preserve Some(0) when
    # a nonzero base was corrected down to zero. None and Some(0) differ.
    total=min(limit,i32(base+delta))
    if base==0 and total<1:return None
    return max(0,total)


def mp_consumption(base,records,weapon):
    """UnitWeapon.GetMpConsumption, RVA 0x61cdc10."""
    return _nullable_cost(base,percentage_delta(records,'MpRate','MpValue',base,weapon),12)


def hp_consumption_rate(base,records,weapon):
    """UnitWeapon.GetHpConsumptionRate, RVA 0x61cdcdc."""
    return _nullable_cost(base,sum_field(records,'HpRate',weapon),100)


def hp_consumption(max_hp,rate):
    """UnitWeapon.GetHpConsumption, RVA 0x61cdd80 (max HP, not current HP)."""
    if rate is None:return None
    from .combat_primitives import resource_percent
    return resource_percent(max_hp,rate)


def sum_shields(shields,field,can_penetrate=False):
    value=0
    for shield in shields:
        if not (can_penetrate and shield['IsPenetrationAffected']):value=i32(value+shield.get(field,0))
    return value


def _conditions(master,set_id):
    if not hasattr(master,'_combat_condition_sets'):
        grouped={}
        for row in master.tables['m_trait_condition']:grouped.setdefault(row['TraitConditionSetId'],[]).append(row)
        master._combat_condition_sets=grouped
    return master._combat_condition_sets.get(set_id,[])


def combat_condition_reason(master,set_id):
    if not set_id:return None
    from .owner_passives import OWNER_FILTERS
    rows=_conditions(master,set_id)
    if len(rows)!=1:return 'combat condition groups not ported'
    row=rows[0]
    if row['TargetTypes'] not in ('Owner','Enemy'):return 'combat active participant selection not ported'
    ignored={'Id','TraitConditionSetId','GroupId','TargetTypes'}|OWNER_FILTERS|{
        'WeaponAttributeTypes','WeaponAttackAttributeTypes','UnitRoleTypes','MapBattleActionTypes'}
    if any(v for k,v in row.items() if k not in ignored):return 'combat condition predicate not ported'
    for field in ['UnitTags','UnitSeries','UnitRoleTypes']:
        if row[field] and not row[field].isdigit():return 'combat numeric expression not ported'
    if row['IsInChanceStep'] or row['IsInOneOnOne']:return 'chance-step/one-on-one context not connected'
    for field,enums in [('WeaponAttributeTypes',WEAPON_ENUM),('WeaponAttackAttributeTypes',ATTACK_ENUM)]:
        if row[field] and not set(row[field].split(','))<=enums.keys():return 'unknown combat weapon attribute'
    if row['TensionTypes'] and not set(row['TensionTypes'].split(','))<={'Normal','High','SuperHigh','Supercharged'}:
        return 'unknown combat tension'
    return None


def dynamic_trait_reason(master,trait):
    if trait['TraitTypeIndex'] not in COMBAT_KINDS|COMBAT_STAT_FIELDS.keys():return 'combat effect not ported'
    if trait['ActionTimingTypeIndex'] or trait['Limit']:return 'consumable/timed combat trait not ported'
    if any(trait[k] for k in ['ValueSourceConditionSetId','TraitBoostConditionSetId','TargetTraitDuplicationGroupId']):
        return 'combat source multiplier/duplication not ported'
    rows=_conditions(master,trait['TargetConditionSetId'])
    if len(rows)!=1 or rows[0]['TargetTypes']!='Owner' or any(v for k,v in rows[0].items()
        if k not in {'Id','TraitConditionSetId','GroupId','TargetTypes'}):return 'combat correction recipient not ported'
    return combat_condition_reason(master,trait['ActiveConditionSetId'])


def needs_combat_stat_context(master,trait):
    """Keep ordinary owner stats in the permanent path; defer battle predicates."""
    if trait['TraitTypeIndex'] not in COMBAT_STAT_FIELDS or not trait['ActiveConditionSetId']:return False
    from .owner_passives import owner_condition_reason
    return owner_condition_reason(master,trait['ActiveConditionSetId']) is not None


def with_combat_stats(unit,selection):
    """Recalculate conditional attack/defense from the uncorrected baseline.

    A shallow view keeps prediction and execution from installing temporary
    corrections on the persistent unit. Reduction rates subsequently subtract
    from the combined rate in reduced_target_stat, before rounding/clamping.
    """
    additions=selection.get('scalar',{})
    if not additions:return unit
    from .owner_passives import unit_stat
    baseline=unit.get('_passive_initial_state')
    if baseline is None:raise NotImplementedError('Conditional combat stats require an uncorrected baseline')
    result=dict(unit);corrections=dict(unit.get('scalar_corrections',{}))
    for field,value in additions.items():corrections[field]=i32(corrections.get(field,0)+value)
    result['scalar_corrections']=corrections
    for stat in ('attack','defense'):
        stem=stat.title()
        if stem+'Rate' in additions:
            result[stat]=unit_stat(baseline[stat],corrections.get(stem+'Rate',0),corrections.get(stem+'Value',0))
    return result


def condition_matches(master,unit,opponent,trait,weapon,action_type,opponent_action_type,weapon_owner_id):
    if not trait['ActiveConditionSetId']:return True
    from .owner_passives import satisfies_owner
    row=_conditions(master,trait['ActiveConditionSetId'])[0]
    candidate=unit if row['TargetTypes']=='Owner' else opponent
    if candidate is None:return False
    # satisfies_owner evaluates the numeric/tag/series fields against candidate;
    # target selection has already been performed above.
    if not satisfies_owner(master,candidate,trait['ActiveConditionSetId']):return False
    if row['UnitRoleTypes'] and master.index['m_unit'][candidate['unit_id']]['RoleTypeIndex']!=int(row['UnitRoleTypes']):return False
    candidate_action=action_type if row['TargetTypes']=='Owner' else opponent_action_type
    if row['MapBattleActionTypes'] and candidate_action not in row['MapBattleActionTypes'].split(','):return False
    for key,field,enums in [('WeaponAttributeTypes','weapon_attributes',WEAPON_ENUM),
                           ('WeaponAttackAttributeTypes','attack_attributes',ATTACK_ENUM)]:
        if row[key]:
            if candidate['id']!=weapon_owner_id:return False
            values={enums[t] for t in row[key].split(',')}
            if 0 not in values and not values.intersection(weapon.get(field,[])):return False
    return True


def runtime_weapon_records(unit):
    """Registered runtime collection; callers supply legacy records separately."""
    return [deepcopy(effect['weapon_correction']) for effect in unit.get('runtime_trait_effects',{}).values()
            if 'weapon_correction' in effect]


def select_combat_corrections(master,unit,opponent,weapon,action_type='Attack',
                              opponent_action_type='NoAction',weapon_owner_id=None):
    if weapon_owner_id is None:weapon_owner_id=unit['id']
    records=[];shields=[];scalars={};penetration=False;applied=[];inactive=[]
    for trait in unit.get('combat_traits',[]):
        if not condition_matches(master,unit,opponent,trait,weapon,action_type,opponent_action_type,weapon_owner_id):
            inactive.append(trait['Id']);continue
        kind=trait['TraitTypeIndex'];value=trait['TraitValue']
        if kind in WEAPON_FIELDS:
            condition=(deepcopy(master.index['m_trait_condition_target_weapon'][trait['TargetWeaponConditionId']])
                       if trait['TargetWeaponConditionId'] else None)
            records.append(dict(condition=condition,**{WEAPON_FIELDS[kind]:1 if kind in (100,101,116) else value}))
            if not matches_weapon(condition,weapon):inactive.append(trait['Id']);continue
        elif kind in COMBAT_STAT_FIELDS:
            field=COMBAT_STAT_FIELDS[kind];scalars[field]=i32(scalars.get(field,0)+value)
        else:
            rows=_conditions(master,trait['ActiveConditionSetId']) if trait['ActiveConditionSetId'] else []
            affected=(kind != 16 and ((value>0 and any(r['WeaponAttributeTypes'] for r in rows)) or any(
                r['WeaponAttackAttributeTypes'] for r in rows)))
            shields.append(dict(id=trait['Id'],IsPenetrationAffected=bool(affected),**{SHIELD_KINDS[kind]:value}))
        applied.append(trait['Id'])
    runtime_applied=[];runtime_inactive=[]
    for identity,effect in unit.get('runtime_trait_effects',{}).items():
        shield=effect.get('shield_correction')
        if shield is not None:
            shields.append(deepcopy(shield));runtime_applied.append(identity)
        record=effect.get('weapon_correction')
        if record is None:continue
        records.append(deepcopy(record))
        (runtime_applied if matches_weapon(record.get('condition'),weapon) else runtime_inactive).append(identity)
    return dict(weapon=records,shields=shields,scalar=scalars,penetration=penetration,applied=applied,inactive=inactive,
                runtime_applied=runtime_applied,runtime_inactive=runtime_inactive)
