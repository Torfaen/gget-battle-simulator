"""Scoped scalar subset of native weapon passive activation.

No permanent unit mutation: original activation is followed by DisableWeaponTraits
within each battle calculation. Unsupported effects retain explicit evidence.
"""
from .original import i32

POWER_KINDS={35,44,45,78,81,82,91,92,112,113}
WEAPON_CORRECTION_KINDS=POWER_KINDS|{97,98,99}
FIELDS={17:'DamageGivenCorrectionRate',18:'DamageGivenCorrectionValue',
        38:'AttackTargetDefenseReductionRate',39:'CriticalDamageGivenCorrectionRate',
        40:'CriticalDamageGivenCorrectionValue',49:'SureHitAttackCount',
        75:'AttackTargetMobilityReductionRate',94:'GuaranteedCriticalCount'}

def corrections(master, unit, opponent, weapon, action_type, opponent_action_type):
    from .owner_passives import pending_reason,satisfies_owner
    from .weapon_corrections import combat_condition_reason,condition_matches,dynamic_trait_reason,WEAPON_FIELDS,matches_weapon
    if not hasattr(master,'_weapon_passive_definitions'):
        sets={}
        for row in master.tables['m_trait_set']:
            sets.setdefault(row['Id'],[]).append(master.index['m_trait'][row['TraitId']])
        details={row['TraitSetId']:row for row in master.tables['m_trait_set_detail']}
        master._weapon_passive_definitions=(sets,details)
    sets,details=master._weapon_passive_definitions
    values={};records=[];debuff=[];applied=[];inactive=[];pending=[];seen=set()
    for source in weapon.get('weapon_traits',[]):
        if source['WeaponTraitTypeIndex']!=3:continue
        key=source.get('Id',source['TargetValue']);set_id=source['TargetValue']
        def reject(reason):pending.append(dict(weapon_trait=key,trait_set=set_id,reason=reason))
        if source.get('ApplyRate',100)!=100:
            reject('probabilistic weapon passive activation not ported');continue
        reason=combat_condition_reason(master,source.get('ApplyConditionSetId',0))
        if reason:reject(reason);continue
        if not condition_matches(master,unit,opponent,
                dict(ActiveConditionSetId=source.get('ApplyConditionSetId',0)),weapon,
                action_type,opponent_action_type,unit['id']):
            inactive.append(key);continue
        detail=details.get(set_id)
        if detail is None or set_id not in sets:
            reject('missing weapon passive master trait set');continue
        if detail.get('DuplicationGroupId') or detail.get('IsStackable') or detail.get('IsEnabledWhileAllTraitsUsable') or detail.get('IsEnabledWithGrantOwnerActive'):
            reject('weapon passive trait-set lifecycle or duplication not ported');continue
        if set_id in seen:
            reject('duplicate weapon passive set identity not ported');continue
        seen.add(set_id)
        for trait in sets[set_id]:
            kind=trait['TraitTypeIndex']
            is_weapon=kind in WEAPON_CORRECTION_KINDS
            reason=dynamic_trait_reason(master,trait) if is_weapon else pending_reason(master,trait)
            if kind not in FIELDS and kind not in (88,89) and not is_weapon:reason='weapon passive correction field not connected'
            if reason:
                pending.append(dict(weapon_trait=key,trait_id=trait['Id'],reason=reason));continue
            if is_weapon:
                if not condition_matches(master,unit,opponent,trait,weapon,action_type,opponent_action_type,unit['id']):
                    inactive.append(trait['Id']);continue
                condition=master.index['m_trait_condition_target_weapon'][trait['TargetWeaponConditionId']] if trait['TargetWeaponConditionId'] else None
                records.append(dict(condition=condition,**{WEAPON_FIELDS[kind]:1 if kind in (100,101) else trait['TraitValue']}))
                if matches_weapon(condition,weapon):applied.append(dict(weapon_trait=key,trait_id=trait['Id']))
                else:inactive.append(trait['Id'])
                continue
            if trait['ActiveConditionSetId'] and not satisfies_owner(master,unit,trait['ActiveConditionSetId']):
                inactive.append(trait['Id']);continue
            if kind in (88,89):
                from .weapon_debuffs import addition_record
                debuff.append(addition_record(trait))
                applied.append(dict(weapon_trait=key,trait_id=trait['Id']));continue
            field=FIELDS[kind];value=1 if kind in (49,94) else trait['TraitValue']
            values[field]=i32(values.get(field,0)+value)
            applied.append(dict(weapon_trait=key,trait_id=trait['Id']))
    return dict(values=values,weapon=records,debuff=debuff,applied=applied,inactive=inactive,pending=pending)
