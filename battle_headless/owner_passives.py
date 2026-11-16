"""Explicit owner-only passive subset; unsupported effects retain reasons and source rows."""
from .tension import current_tension
from .unit_life import is_alive
from copy import deepcopy
from dataclasses import asdict
from .mechanics import SupportBudget, character_stat, npc_passive_definitions
from .combat_primitives import ScalarCorrections, initial_mp, resource_percent, same_group
from .original import i32, round_down, div
from .conditions import hp_threshold,en_threshold,required_flag
from .original import OriginalScalarKernel
from .action_constraints import INABILITY_FIELDS
from .terrain_capability import TERRAIN_CHANGE_TYPES,change_record

# Original CreateCorrectionInternal setter bindings. Source methods are retained
# in outputs/battle_headless/extensions/scalar_trait_bindings.json.
BINDINGS={2:'EvasionRate',7:'AttackRate',8:'AttackValue',9:'DefenseRate',10:'DefenseValue',
    11:'MobilityRate',12:'MobilityValue',17:'DamageGivenCorrectionRate',18:'DamageGivenCorrectionValue',
    19:'ChanceStepMaxCount',
    21:'CharacterRangedRate',22:'CharacterRangedValue',23:'CharacterMeleeRate',24:'CharacterMeleeValue',
    25:'CharacterDefenseRate',26:'CharacterDefenseValue',27:'CharacterReactionRate',28:'CharacterReactionValue',
    29:'CharacterAwakenRate',30:'CharacterAwakenValue',
    31:'MaxHpRate',32:'MaxHpValue',33:'MaxEnRate',34:'MaxEnValue',39:'CriticalDamageGivenCorrectionRate',
    38:'AttackTargetDefenseReductionRate',75:'AttackTargetMobilityReductionRate',
    40:'CriticalDamageGivenCorrectionValue',46:'InitialMp',51:'SupportAttackAndCounterLimit',
    49:'SureHitAttackCount',50:'AbsoluteEvasionCount',52:'SupportDefenseLimit',64:'MpFluctuationRate',70:'MovementDistanceValue',
    76:'AntiAttackEvadedMpReductionCount',77:'AntiMpChangeCount',79:'DamageNegationValue',96:'MpLimitBreakCount',
    80:'GuaranteedChanceStepCount',85:'GuaranteedChanceStepWeaponUsedOnlyCount',
    84:'UnbreakableCount',94:'GuaranteedCriticalCount',114:'ParryRate',115:'PostMapWeaponAttackMoveCount',
    86:'SupportAttackAndCounterAdaptiveLimit',87:'SupportDefenseAdaptiveLimit'}
BINDINGS.update(INABILITY_FIELDS)


def account_traits(master, configuration):
    from .trait_sources import account_initial_sources
    sources=account_initial_sources(master,None,configuration,
        now_ms=configuration.get('trait_source_time_ms'))
    rows=[]
    for source in sources:
        source_kind=source['source_kind'];trait_set=source['master_trait_set_id']
        for link in master.tables['m_trait_set']:
            if link['Id']==trait_set:
                trait=deepcopy(master.index['m_trait'][link['TraitId']])
                if trait['TraitTypeIndex'] in TERRAIN_CHANGE_TYPES:
                    trait['_terrain_source_kind']='option_parts' if source_kind=='option_parts' else 'ability'
                rows.append(trait)
    return rows


OWNER_FILTERS={'UnitTags','UnitSeries','TensionTypes','HpRateLteThreshold','HpRateGteThreshold',
    'UseHpRateLteThreshold','IsHpDamaged','EnRateLteThreshold','EnRateGteThreshold',
    'EnValueLteThreshold','EnValueGteThreshold','IsInChanceStep','IsInOneOnOne'}


def owner_condition_reason(master,set_id):
    rows=[r for r in master.tables['m_trait_condition'] if r['TraitConditionSetId']==set_id]
    if len(rows)!=1:return 'owner condition groups not ported'
    row=rows[0]
    if row['TargetTypes']!='Owner':return 'non-owner active participants not ported'
    ignored={'Id','TraitConditionSetId','GroupId','TargetTypes'}|OWNER_FILTERS
    if any(v for k,v in row.items() if k not in ignored):return 'owner condition predicate not ported'
    for field in ['UnitTags','UnitSeries']:
        if row[field] and not row[field].isdigit():return 'compound tag/series expression not ported'
    if row['IsInChanceStep'] or row['IsInOneOnOne']:return 'battle participant context not connected'
    if row['TensionTypes'] and not set(row['TensionTypes'].split(','))<={'Normal','High','SuperHigh','Supercharged'}:
        return 'unknown tension predicate'
    return None


def satisfies_owner(master,unit,set_id):
    row=next(r for r in master.tables['m_trait_condition'] if r['TraitConditionSetId']==set_id)
    if row['UnitTags'] and not any(r['UnitId']==unit['unit_id'] and r['LineageId']==int(row['UnitTags'])
                                  for r in master.tables['m_unit_lineage']):return False
    if row['UnitSeries']:
        series_set=master.index['m_unit'][unit['unit_id']]['SeriesSetId']
        if not any(r['SeriesSetId']==series_set and r['SeriesId']==int(row['UnitSeries'])
                   for r in master.tables['m_series_set']):return False
    if row['TensionTypes']:
        enums={'Normal':2,'High':3,'SuperHigh':4,'Supercharged':5}
        if current_tension(unit) not in {enums[n] for n in row['TensionTypes'].split(',')}:return False
    lte=row['HpRateLteThreshold'] if row['HpRateLteThreshold'] or row['UseHpRateLteThreshold'] else None
    if not hp_threshold(unit['hp'],unit['max_hp'],lte=lte,gte=row['HpRateGteThreshold'] or None,
                        damaged=row['IsHpDamaged']):return False
    return en_threshold(unit['en'],unit['max_en'],rate_lte=row['EnRateLteThreshold'] or None,
        rate_gte=row['EnRateGteThreshold'] or None,value_lte=row['EnValueLteThreshold'] or None,
        value_gte=row['EnValueGteThreshold'] or None)


def pending_reason(master, trait):
    kind=trait['TraitTypeIndex']
    if kind not in BINDINGS and kind not in (88,89,93) and kind not in TERRAIN_CHANGE_TYPES:return 'effect executor not ported'
    if kind in TERRAIN_CHANGE_TYPES and trait.get('_terrain_source_kind')=='option_parts':
        return 'option-parts terrain override uses the status-source path'
    if trait['ActionTimingTypeIndex']!=0:return 'timed lifecycle not ported'
    if kind in (76,77,96) and trait['Limit']:return 'limited MP-control lifecycle not ported'
    if kind in (80,85,115) and trait['Limit']:return 'limited chance-step/movement lifecycle not ported'
    if kind in INABILITY_FIELDS and trait['Limit']:return 'limited inability lifecycle not ported'
    if kind in TERRAIN_CHANGE_TYPES and trait['Limit']:return 'limited terrain capability lifecycle not ported'
    if kind in (84,114) and trait['Limit']:
        return 'limited parry lifecycle not ported' if kind==114 else 'limited unbreakable lifecycle not ported'
    if trait['ActiveConditionSetId']:
        reason=owner_condition_reason(master,trait['ActiveConditionSetId'])
        if reason:return reason
        if BINDINGS.get(kind,'').startswith(('MaxHp','MaxEn','InitialMp')):
            return 'conditional resource-maximum lifecycle not ported'
    if trait['ValueSourceConditionSetId'] or trait['TraitBoostConditionSetId']:
        return 'source/boost multiplier not ported'
    if trait['TargetWeaponConditionId']:return 'targeted weapon correction not ported'
    if trait['TargetTraitDuplicationGroupId'] and kind not in (88,89):return 'trait duplication selection not ported'
    conditions=[r for r in master.tables['m_trait_condition'] if r['TraitConditionSetId']==trait['TargetConditionSetId']]
    if len(conditions)!=1:return 'target condition groups not ported'
    row=conditions[0]
    if row['TargetTypes']!='Owner':return 'non-owner target selection not ported'
    ignored={'Id','TraitConditionSetId','GroupId','TargetTypes'}
    if any(v for k,v in row.items() if k not in ignored):return 'filtered owner condition not ported'
    return None


def unit_stat(base,rate=0,flat=0,minimum=0,maximum=9999999):
    return max(minimum,min(maximum,i32(base+flat+round_down(div(i32(base*rate),100)))))


def is_own_phase_hp_recovery(master,trait):
    if trait['TraitTypeIndex']!=3 or trait['ActionTimingTypeIndex']!=2 or trait['Limit']!=0:return False
    if any(trait[k] for k in ['ActiveConditionSetId','ValueSourceConditionSetId','TargetWeaponConditionId',
                              'TraitBoostConditionSetId','TargetTraitDuplicationGroupId']):return False
    rows=[r for r in master.tables['m_trait_condition'] if r['TraitConditionSetId']==trait['TargetConditionSetId']]
    return (len(rows)==1 and rows[0]['TargetTypes'] in ('Owner','SameGroup') and not any(v for k,v in rows[0].items()
        if k not in {'Id','TraitConditionSetId','GroupId','TargetTypes'}) and trait['TraitValue']>=0)


def _own_phase_start(env,stage):
    effects=[]
    original_clock=getattr(env,'phase_clock',None)
    if original_clock and original_clock.side not in (0,1):return effects
    for unit in env.units.values():
        if not is_alive(unit) or (not original_clock and unit['team']!=env.active_team):continue
        for trait in unit.get('_owner_traits',[]):
            if not is_own_phase_hp_recovery(stage.master,trait):continue
            condition=next(r for r in stage.master.tables['m_trait_condition'] if r['TraitConditionSetId']==trait['TargetConditionSetId'])
            targets=([unit] if condition['TargetTypes']=='Owner' else [target for target in env.units.values()
                if is_alive(target) and same_group(unit.get('formation_group'),target.get('formation_group'))])
            for target in targets:
                before=target['hp'];amount=resource_percent(target['max_hp'],trait['TraitValue'])
                target['hp']=min(target['max_hp'],before+amount)
                effects.append(dict(owner=unit['id'],unit=target['id'],trait_id=trait['Id'],timing=2,hp_before=before,
                                    hp_after=target['hp'],amount=target['hp']-before))
    return effects


def _refresh(env,stage,initialize=False):
    npcs={n['placement']['Id']:n for n in stage.npcs}
    summary=[]
    for unit in env.units.values():
        if '_owner_traits' in unit:
            traits=unit['_owner_traits']
        elif 'account_configuration' in unit:
            traits=account_traits(stage.master,unit['account_configuration'])
        else:
            npc=npcs[unit['source_npc_id']]
            traits=npc_passive_definitions(stage.master,npc['units'][0],npc['characters'][0])
        unit['_owner_traits']=traits
        # Cache only permanent source traits. Temporary corrections are rebuilt
        # each refresh, so repeated observations neither stack nor retain them.
        traits=traits+[entry['trait'] for entry in unit.get('active_skill_effects',[])
                      if entry['skill_kind'] not in (25,26,27)]  # Separate native once slots, not permanent limits.
        traits=traits+[entry['trait'] for entry in unit.get('active_weapon_debuffs',[])]
        corrections=ScalarCorrections();applied=[];pending=[]
        inactive=[];timed=[];combat=[];damage_caps=[];debuff_additions=[];terrain_changes=[];parts_terrain_changes=[]
        for trait in traits:
            if trait['TraitTypeIndex'] in TERRAIN_CHANGE_TYPES and trait.get('_terrain_source_kind')=='option_parts':
                parts_terrain_changes.append(change_record(trait));applied.append(trait['Id']);continue
            if is_own_phase_hp_recovery(stage.master,trait):timed.append(trait['Id']);continue
            from .weapon_corrections import COMBAT_KINDS,dynamic_trait_reason,needs_combat_stat_context
            if trait['TraitTypeIndex'] in COMBAT_KINDS or needs_combat_stat_context(stage.master,trait):
                reason=dynamic_trait_reason(stage.master,trait)
                if reason:pending.append(dict(id=trait['Id'],reason=reason,source=trait))
                else:combat.append(trait)
                continue
            reason=pending_reason(stage.master,trait)
            if reason:pending.append(dict(id=trait['Id'],reason=reason,source=trait));continue
            if trait['ActiveConditionSetId'] and not satisfies_owner(stage.master,unit,trait['ActiveConditionSetId']):
                inactive.append(trait['Id']);continue
            if trait['TraitTypeIndex']==93:
                damage_caps.append(trait['TraitValue']);applied.append(trait['Id']);continue
            if trait['TraitTypeIndex'] in TERRAIN_CHANGE_TYPES:
                terrain_changes.append(change_record(trait));applied.append(trait['Id']);continue
            if trait['TraitTypeIndex'] in (88,89):
                from .weapon_debuffs import addition_record
                debuff_additions.append(addition_record(trait));applied.append(trait['Id']);continue
            corrections.add({BINDINGS[trait['TraitTypeIndex']]:
                1 if trait['TraitTypeIndex'] in {49,50,76,77,80,84,85,94,96,115}|INABILITY_FIELDS.keys() else trait['TraitValue']})
            applied.append(trait['Id'])
        from .supporters import corrections_for_unit
        support_applied,support_inactive,support_pending=corrections_for_unit(
            stage.master,unit,getattr(env,'supporter_sources',[]))
        for index,effect in enumerate(support_applied):
            value={effect['field']:effect['value']}
            # _AddCorrection keys max-group entries by generated IngameTraitId,
            # not by supporter or master trait ID (which can be reused).
            if effect.get('max_group'):corrections.replace_group('supporter:'+str(index),value)
            else:corrections.add(value)
        unit.update(supporter_effects=support_applied,inactive_supporter_effects=support_inactive,
                    pending_supporter_effects=support_pending)
        c=corrections.total()
        unit['_runtime_scalar_base_corrections']=dict(c)
        from .trait_effects import runtime_stat_corrections
        c=runtime_stat_corrections(unit,c)
        if '_passive_initial_state' not in unit:
            unit['_passive_initial_state']=deepcopy({k:unit[k] for k in
                ['hp','max_hp','en','max_en','attack','defense','mobility','movement','pilot','mp']})
        base=unit['_passive_initial_state']
        for stat in ['attack','defense','mobility']:
            stem=stat.title();unit[stat]=unit_stat(base[stat],c.get(stem+'Rate',0),c.get(stem+'Value',0))
        for stat,stem in [('hp','MaxHp'),('en','MaxEn')]:
            unit['max_'+stat]=unit_stat(base['max_'+stat],c.get(stem+'Rate',0),c.get(stem+'Value',0),1,
                                       99999999 if stat=='hp' else 9999999)
            if initialize:unit[stat]=unit['max_'+stat]
        unit['movement']=unit_stat(base['movement'],flat=c.get('MovementDistanceValue',0),maximum=9999)
        for stat in ['Ranged','Melee','Defense','Reaction','Awaken']:
            unit['pilot'][stat]=character_stat(base['pilot'][stat],c.get('Character'+stat+'Rate',0),c.get('Character'+stat+'Value',0))
        if initialize and unit['team']=='player':unit['mp']=initial_mp(c.get('InitialMp',0))
        budget=SupportBudget(**unit.get('support_budget',{})) if not initialize else SupportBudget()
        budget.attack_limit=c.get('SupportAttackAndCounterLimit',0)
        budget.defense_limit=c.get('SupportDefenseLimit',0)
        budget.attack_adaptive_limit=c.get('SupportAttackAndCounterAdaptiveLimit',0)
        budget.defense_adaptive_limit=c.get('SupportDefenseAdaptiveLimit',0)
        has_equipped_parts=bool(unit.get('account_configuration',{}).get('option_parts_id'))
        unit.update(option_parts_terrain_changes=parts_terrain_changes if has_equipped_parts or parts_terrain_changes else None,
                    registered_terrain_capability_changes=terrain_changes,
                    pending_terrain_capability_changes=[p for p in pending if p['source']['TraitTypeIndex'] in TERRAIN_CHANGE_TYPES],
                    weapon_debuff_additions=debuff_additions,damage_taken_upper_limit_values=damage_caps,scalar_corrections=c,support_budget=asdict(budget),passive_traits=traits,
                    applied_passive_ids=applied,inactive_passive_ids=inactive,timed_passive_ids=timed,pending_passives=pending,
                    combat_traits=combat)
        summary.append(dict(unit=unit['id'],applied=applied,inactive=inactive,
                            timed=timed,combat=[t['Id'] for t in combat],pending=[dict(id=p['id'],reason=p['reason']) for p in pending]))
    env.rules.name='original-scalars-mt19937-explicit-owner-passive-subset'
    return summary


def attach_owner_passives(env,stage):
    if env.events:raise ValueError('Owner initialization passives must be attached before battle starts')
    from .trait_sources import attach_initial_source_plans
    attach_initial_source_plans(env,stage)
    group_rows={r['Id']:r for r in stage.source['m_map_group']}
    for unit in env.units.values():
        if 'account_configuration' in unit:
            unit['formation_group']=dict(link_type=2,side=0,id='account:'+str(unit['account_configuration']['force']))
            # UserUnitToIngameUnitConverter writes false at ctor stack+0x18
            # (0x621f180); supporter leader skills are a separate concept.
            unit['is_leader']=False
        else:
            links=[r for r in stage.master.tables['m_map_group_npc'] if r['MapNpcId']==unit['source_npc_id'] and r['MapGroupId'] in group_rows]
            if len(links)!=1:raise NotImplementedError('NPC requires one explicit map group membership')
            row=group_rows[links[0]['MapGroupId']]
            unit['formation_group']=dict(link_type=row['MapGroupTypeIndex'],side=2,id='npc:'+str(row['Id']))
            # MasterMapStageNpc._ConvertCore, 0x6003a10/0x6003ae8:
            # GroupUnitType == Leader (2), passed through NPC/unit constructors.
            unit['is_leader']=links[0]['GroupUnitTypeIndex']==2
    result=_refresh(env,stage,initialize=True)
    env._refresh_state=lambda:_refresh(env,stage)
    from .weapon_debuffs import attach_weapon_debuffs
    attach_weapon_debuffs(env)
    env._phase_passive_effects=lambda:_own_phase_start(env,stage)
    if not getattr(env,'original_actions',False):env._after_phase_start=env._phase_passive_effects
    return result
