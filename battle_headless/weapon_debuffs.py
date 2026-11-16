"""Original ordinary weapon-debuff grant and turn-lifetime subset.

Grouped additions remain distinct from scalar corrections. Complex sets,
source registration, duplication across other grant services and MAP remain pending.
"""
from .original import i32


def addition_for_group(records, duplication_group):
    """TraitCorrection.GetWeaponDebuffAddition, with canonical group values.

    Matching contributions sum rather than taking a maximum or first match.
    A zero/zero total returns no correction, including after cancellation or
    Int32 wraparound. This does not create or mutate a granted trait set.
    """
    value=duration=0
    for record in records:
        if record['duplication_group']!=duplication_group:
            continue
        value=i32(value+record.get('value',0))
        duration=i32(duration+record.get('duration',0))
    if value==0 and duration==0:
        return None
    return dict(duplication_group=duplication_group,value=value,duration=duration)


def higher_priority(value,limit,unlimited,other_value,other_limit,other_unlimited):
    """Native IngameTrait._IsHigherPriority, including unsigned magnitudes."""
    magnitude=abs(i32(value))&0xffffffff
    other_magnitude=abs(i32(other_value))&0xffffffff
    if magnitude!=other_magnitude:
        return magnitude>other_magnitude
    if other_unlimited:
        return False
    return bool(unlimited or i32(limit)>i32(other_limit))


def can_overwrite_trait(current,new_trait,*,stack_count=1,additional_value=0,additional_limit=0):
    """Original IngameTrait.CanBeOverwrittenBy normalized getter inputs.

    `limit` is the current total limit; `base_limit` decides unlimited status.
    Set-level duplicate discovery and first-trait selection remain external.
    """
    return higher_priority(i32(new_trait['TraitValue']*stack_count+additional_value),
        i32(new_trait['Limit']+additional_limit),new_trait['Limit']==0,
        i32(current['base_value']+current.get('additional_value',0)),
        i32(current['limit']-current.get('used_count',0)),current['base_limit']==0)


def addition_record(trait):
    return dict(duplication_group=trait['TargetTraitDuplicationGroupId'] or None,
                value=trait['TraitValue'] if trait['TraitTypeIndex']==88 else 0,
                duration=trait['TraitValue'] if trait['TraitTypeIndex']==89 else 0)


def effective_limit(base_limit,additional_limit):
    """IngameTrait.GetLimit leaves a zero base limit unlimited."""
    return i32(base_limit+additional_limit) if i32(base_limit) else 0


def prepare_debuffs(master,attacker,target,weapon,action_type,opponent_action_type):
    """Resolve deterministic ordinary weapon debuffs without granting them."""
    from .owner_passives import pending_reason
    from .weapon_corrections import COMBAT_KINDS,dynamic_trait_reason,combat_condition_reason,condition_matches
    if not hasattr(master,'_weapon_debuff_definitions'):
        sets={}
        for row in master.tables['m_trait_set']:
            sets.setdefault(row['Id'],[]).append(master.index['m_trait'][row['TraitId']])
        master._weapon_debuff_definitions=(sets,{r['TraitSetId']:r for r in master.tables['m_trait_set_detail']})
    sets,details=master._weapon_debuff_definitions
    enabled=[];pending=[];inactive=[]
    for source in weapon.get('weapon_traits',[]):
        if source['WeaponTraitTypeIndex']!=12:continue
        key=source['Id'];set_id=source['TargetValue'];reason=None
        detail=details.get(set_id);rows=sets.get(set_id,[])
        if source.get('ApplyRate',100)!=100:reason='probabilistic weapon debuff not ported'
        elif detail is None or not rows:reason='missing weapon debuff master set'
        elif len(rows)!=1 or detail.get('IsStackable') or detail.get('IsEnabledWhileAllTraitsUsable') or detail.get('IsEnabledWithGrantOwnerActive'):
            reason='complex weapon debuff set lifecycle not ported'
        else:reason=combat_condition_reason(master,source.get('ApplyConditionSetId',0))
        if not reason:
            row=rows[0]
            if row['ActionTimingTypeIndex'] not in (0,3):reason='weapon debuff timing not ported'
            elif row['TraitTypeIndex'] in (31,32,33,34,46,88,89):reason='weapon debuff resource/source lifecycle not ported'
            else:
                normalized=dict(row,ActionTimingTypeIndex=0,Limit=0)
                reason=(dynamic_trait_reason(master,normalized) if row['TraitTypeIndex'] in COMBAT_KINDS else pending_reason(master,normalized))
        if reason:
            pending.append(dict(weapon_trait=key,trait_set=set_id,reason=reason));continue
        if not condition_matches(master,attacker,target,dict(ActiveConditionSetId=source.get('ApplyConditionSetId',0)),
                                 weapon,action_type,opponent_action_type,attacker['id']):
            inactive.append(key);continue
        enabled.append(dict(weapon_trait=key,trait_set=set_id,duplication_group=detail['DuplicationGroupId'] or None,
                            master_trait=dict(rows[0])))
    return dict(enabled=enabled,pending=pending,inactive=inactive)


def grant_debuffs(env,attacker,target,result):
    """AttackUseCase grants after a successful, non-parried strike."""
    prepared=result.get('weapon_debuffs',{});grants=[];rejected=[]
    if not result.get('hit') or result.get('parry'):return
    if not prepared.get('enabled'):return
    if not hasattr(env,'weapon_debuff_profile'):
        result['weapon_debuff_grants']=dict(applied=[],pending=[dict(reason='weapon debuff runtime not attached')]);return
    additions=attacker.get('weapon_debuff_additions',[])+result.get('weapon_passives',{}).get('debuff',[])
    active=target.setdefault('active_weapon_debuffs',[])
    for source in prepared['enabled']:
        key=str(source['weapon_trait']);counts=attacker.setdefault('weapon_trait_active_counts',{})
        counts[key]=i32(counts.get(key,0)+1)
        group=source['duplication_group'];master_trait=source['master_trait']
        addition=addition_for_group(additions,group) or dict(value=0,duration=0)
        old=next((entry for entry in active if group is not None and entry['duplication_group']==group),None)
        if old is not None and not can_overwrite_trait(old,master_trait,additional_value=addition['value'],additional_limit=addition['duration']):
            rejected.append(dict(weapon_trait=source['weapon_trait'],reason='existing weapon debuff has priority'));continue
        if old is not None:active.remove(old)
        base_limit=master_trait['Limit']
        limit=effective_limit(base_limit,addition['duration'])
        trait=dict(master_trait,ActionTimingTypeIndex=0,Limit=0,
                   TraitValue=i32(master_trait['TraitValue']+addition['value']))
        entry=dict(weapon_trait=source['weapon_trait'],trait_set=source['trait_set'],duplication_group=group,
            trait=trait,base_value=master_trait['TraitValue'],additional_value=addition['value'],
            limit=limit,used_count=0,base_limit=base_limit,action_timing=master_trait['ActionTimingTypeIndex'],
            source_unit=attacker['id'])
        active.append(entry)
        grants.append(dict(weapon_trait=source['weapon_trait'],trait_id=trait['Id'],value=trait['TraitValue'],
                           limit=limit,unlimited=base_limit==0,replaced=old is not None))
    result['weapon_debuff_grants']=dict(applied=grants,rejected=rejected,pending=prepared.get('pending',[]))
    if grants:env._refresh_state()


def attach_weapon_debuffs(env):
    env._weapon_debuff_last_turn=env.turn
    if hasattr(env,'weapon_debuff_profile'):return
    env.weapon_debuff_profile='ordinary-single-trait-turn-debuff-subset'
    previous=getattr(env,'_after_turn_progress',None)
    def progress(record):
        if previous is not None:previous(record)
        elapsed=max(0,record['turn']-env._weapon_debuff_last_turn)
        env._weapon_debuff_last_turn=record['turn'];expired=[]
        if not elapsed:return
        for unit in env.units.values():
            active=[]
            for entry in unit.get('active_weapon_debuffs',[]):
                if entry['action_timing']==3 and entry['base_limit']:
                    entry['used_count']=i32(entry['used_count']+elapsed)
                    if entry['used_count']>=entry['limit']:
                        expired.append(dict(unit=unit['id'],trait_id=entry['trait']['Id']));continue
                active.append(entry)
            unit['active_weapon_debuffs']=active
        if expired:record['expired_weapon_debuffs']=expired;env._refresh_state()
    env._after_turn_progress=progress
