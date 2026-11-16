"""Concrete combat corrections for explicitly resolved runtime owner grants.

Builder evidence includes weapon_trait_builder_verification.json and resource
trait first-execution evidence. Resource maxima/initial MP forbid ordinary
removal; the other supported combat subclasses use the base removal behavior.
Grant/stack/activation selection is separate.
"""
from copy import deepcopy
from .trait_consumption import is_active,remaining_value
from .mechanics import short
from .original import i32
from .weapon_corrections import WEAPON_FIELDS, SHIELD_KINDS, _conditions
from .trait_resource_effects import RESOURCE_FIELDS,resource_correction,resource_on_execute,cannot_remove_resource

VERIFIED_WEAPON_KINDS=set(WEAPON_FIELDS)
VERIFIED_SHIELD_KINDS={14,15,16}
VERIFIED_STAT_KINDS={2:'EvasionRate',7:'AttackRate',8:'AttackValue',9:'DefenseRate',10:'DefenseValue',
    11:'MobilityRate',12:'MobilityValue',17:'DamageGivenCorrectionRate',18:'DamageGivenCorrectionValue',
    21:'CharacterRangedRate',22:'CharacterRangedValue',23:'CharacterMeleeRate',24:'CharacterMeleeValue',
    25:'CharacterDefenseRate',26:'CharacterDefenseValue',27:'CharacterReactionRate',28:'CharacterReactionValue',
    29:'CharacterAwakenRate',30:'CharacterAwakenValue',38:'AttackTargetDefenseReductionRate',
    39:'CriticalDamageGivenCorrectionRate',40:'CriticalDamageGivenCorrectionValue',75:'AttackTargetMobilityReductionRate'}
# Constructor inheritance retained in extensions/*Trait.json; these classes
# belong to the on-demand repository, even when their authored timing is zero.
KNOWN_ON_DEMAND_KINDS=frozenset({3,5,13,43,83})


def runtime_stat_corrections(unit,base,*,excluding=None):
    result=dict(base)
    for identity,effect in unit.get('runtime_trait_effects',{}).items():
        if identity==excluding:continue
        for field,value in effect.get('stat_correction',{}).items():
            result[field]=i32(result.get(field,0)+value)
    return result


def refresh_runtime_stats(unit,*,excluding=None):
    from .owner_passives import unit_stat
    from .mechanics import character_stat
    baseline=unit.get('_passive_initial_state',{})
    base=unit.setdefault('_runtime_scalar_base_corrections',dict(unit.get('scalar_corrections',{})))
    values=runtime_stat_corrections(unit,base,excluding=excluding)
    fields=set(unit.get('_runtime_stat_fields',()))
    for effect in unit.get('runtime_trait_effects',{}).values():fields.update(effect.get('stat_correction',{}))
    unit['_runtime_stat_fields']=sorted(fields)
    unit['scalar_corrections']=values
    for stat in ('attack','defense','mobility'):
        stem=stat.title()
        if {stem+'Rate',stem+'Value'}&fields:
            unit[stat]=unit_stat(baseline[stat],values.get(stem+'Rate',0),values.get(stem+'Value',0))
    for stat,stem,maximum in (('hp','MaxHp',99999999),('en','MaxEn',9999999)):
        if {stem+'Rate',stem+'Value'}&fields:
            unit['max_'+stat]=unit_stat(baseline['max_'+stat],values.get(stem+'Rate',0),
                                       values.get(stem+'Value',0),1,maximum)
    for stem in ('Ranged','Melee','Defense','Reaction','Awaken'):
        if {'Character'+stem+'Rate','Character'+stem+'Value'}&fields:
            unit['pilot'][stem]=character_stat(baseline['pilot'][stem],
                values.get('Character'+stem+'Rate',0),values.get('Character'+stem+'Value',0))
    if 'ChanceStepMaxCount' in fields:
        unit['chance_step']['correction']=values.get('ChanceStepMaxCount',0)
    for field,key in (('SupportAttackAndCounterLimit','attack_limit'),('SupportDefenseLimit','defense_limit')):
        if field in fields:unit['support_budget'][key]=values.get(field,0)


def _validate_stat_baseline(unit,field):
    base=unit.get('_passive_initial_state',{})
    for stat,stem in (('hp','MaxHp'),('en','MaxEn')):
        if field in (stem+'Rate',stem+'Value') and any(key not in base or key not in unit
                for key in (stat,'max_'+stat)):
            raise NotImplementedError('Original uncorrected resource baseline required')
    if field=='InitialMp' and 'mp' not in unit:raise NotImplementedError('Original current MP required')
    for stat in ('attack','defense','mobility'):
        if field in (stat.title()+'Rate',stat.title()+'Value') and stat not in base:
            raise NotImplementedError('Original uncorrected stat baseline required')
    for stem in ('Ranged','Melee','Defense','Reaction','Awaken'):
        if field in ('Character'+stem+'Rate','Character'+stem+'Value') and (
                stem not in base.get('pilot',{}) or stem not in unit.get('pilot',{})):
            raise NotImplementedError('Original uncorrected pilot baseline required')


def repeat_resolved_unconditional_always(groups,units,target_id):
    """Already registered owner corrections have no repeat side effect.

    IngameTrait.Execute -> _AddCorrection -> OnExecuteRepeatable (null) for
    these verified subclasses. Known on-demand classes are excluded from the always
    repository. Do not treat HP-change on-demand traits as unconditional heals.
    Conditional activation/aura/unknown classes require the full service.
    """
    from .trait_consumption import is_set_enabled
    units=list(units);by_id={unit['id']:unit for unit in units};visited=[];selected=[];parents={}
    for group in groups.values():
        values=group['values']
        if all(t['trait_type'] in KNOWN_ON_DEMAND_KINDS for t in values):continue
        if any(t['trait_type'] not in VERIFIED_WEAPON_KINDS|VERIFIED_SHIELD_KINDS|
               VERIFIED_STAT_KINDS.keys()|RESOURCE_FIELDS.keys()|{19} for t in values):
            raise NotImplementedError('Always-active concrete class/recipient selection required')
        if not group.get('effect_registration_complete') or group.get('prediction'):
            raise NotImplementedError('Always-active registration required')
        if group['owner_unit_id']!=target_id:continue  # Registered subset is owner-only.
        if not is_set_enabled(group):
            raise NotImplementedError('Always-active enable transition required')
        owner=by_id[target_id]
        for trait in values:
            if trait['runtime_trait_id'] not in owner.get('runtime_trait_effects',{}):
                raise NotImplementedError('Always-active correction reactivation required')
            selected.append(trait);parents[trait['runtime_trait_id']]=group
    from types import SimpleNamespace
    from .trait_initial_lifecycle import execute_always_on_battle
    # This caller has already validated the unconditional, registered owner
    # subset. The full service supplies its own context/condition providers.
    services=SimpleNamespace(create_context=lambda battle:None,always_traits=lambda:selected,
        active=lambda trait,context,battle:True,parent=parents.get,
        target=lambda trait,unit,context,battle:trait['owner_unit_id']==unit['id'],
        is_aura=lambda group:False,boost_count=lambda trait,owner:None,
        execute=lambda trait,unit,boost:visited.append(trait['runtime_trait_id']),
        remove=lambda trait,unit:None,return_contexts=lambda:None)
    execute_always_on_battle(services,[by_id[target_id]])
    return dict(repeated_runtime_trait_ids=visited)


def register_resolved_owner_combat_set(master,group,unit):
    """Register an already selected, enabled owner combat group, atomically.

    Never imports permanent account sources implicitly or guesses activation,
    source scaling, duplication arbitration or prediction recipients.
    """
    if group['owner_unit_id']!=unit['id']:raise ValueError('Owner recipient mismatch')
    if group.get('prediction'):raise NotImplementedError('Prediction requires a separate recipient repository')
    if not group['enabled_internal']:raise NotImplementedError('Disabled group activation not connected')
    if group.get('effect_registration_complete'):raise ValueError('Group effects already registered')
    registry=unit.get('runtime_trait_effects',{})
    prepared={}
    legacy={row['Id'] for row in unit.get('combat_traits',[])}
    legacy.update(unit.get('applied_passive_ids',()))
    for trait in group['values']:
        row=trait['master_trait'];identity=trait['runtime_trait_id'];kind=trait['trait_type']
        if identity in registry or identity in prepared:raise ValueError('Duplicate runtime trait identity')
        if row['Id'] in legacy:raise ValueError('Trait already supplied by legacy combat source')
        if kind not in VERIFIED_WEAPON_KINDS|VERIFIED_SHIELD_KINDS|VERIFIED_STAT_KINDS.keys()|RESOURCE_FIELDS.keys()|{19,51,52}:
            raise NotImplementedError('Concrete combat builder not verified')
        if not is_active(trait):raise NotImplementedError('Exhausted initial grant selection not connected')
        if any(row[key] for key in ('ActiveConditionSetId','ValueSourceConditionSetId',
                                   'TraitBoostConditionSetId','TargetTraitDuplicationGroupId')):
            raise NotImplementedError('Runtime activation/source/duplication selection required')
        targets=_conditions(master,row['TargetConditionSetId'])
        if len(targets)!=1 or targets[0]['TargetTypes']!='Owner' or any(
                value for key,value in targets[0].items()
                if key not in {'Id','TraitConditionSetId','GroupId','TargetTypes'}):
            raise NotImplementedError('Runtime recipient selection required')
        effect=dict(master_trait_id=row['Id'],can_remove=_can_remove,remove=_on_remove)
        if kind in RESOURCE_FIELDS:
            if row['TargetWeaponConditionId']:raise NotImplementedError('Resource weapon condition routing required')
            _validate_stat_baseline(unit,RESOURCE_FIELDS[kind])
            effect.update(stat_correction=resource_correction(trait),can_remove=cannot_remove_resource,
                          remove=_remove_stat)
        elif kind in VERIFIED_STAT_KINDS:
            if row['TargetWeaponConditionId']:raise NotImplementedError('Stat weapon condition routing required')
            # Awaken alone has a distinct native buff channel, not an ordinary
            # additive base correction. Range recipients were rejected above.
            if kind in (29,30) and i32(group['source_type']) in (5,6,9,14,15,19):
                raise NotImplementedError('Awaken buff-channel calculation required')
            _validate_stat_baseline(unit,VERIFIED_STAT_KINDS[kind])
            effect.update(stat_correction={VERIFIED_STAT_KINDS[kind]:trait['trait_value']},remove=_remove_stat)
        elif kind==19:
            timing=trait['action_timing']
            if timing not in (15,23):raise NotImplementedError('Runtime scalar chance-step correction not connected')
            if row['TargetWeaponConditionId']:raise NotImplementedError('Chance-step weapon recipient not connected')
            counter='once' if timing==15 else 'guaranteed_once'
            unit['chance_step'][counter]  # Validate original counter before any group mutation.
            effect.update(chance_step_counter=counter,remove=_remove_chance_step)
        elif kind in (51,52):
            if trait['action_timing']!={51:16,52:17}[kind]:
                raise NotImplementedError('Resolved support grant requires original one-time timing')
            if row['TargetWeaponConditionId']:raise NotImplementedError('Support weapon recipient not connected')
            counter='attack_once' if kind==51 else 'defense_once'
            unit['support_budget'][counter]
            effect.update(support_counter=counter,remove=_remove_support)
        elif kind in VERIFIED_WEAPON_KINDS:
            condition=(deepcopy(master.index['m_trait_condition_target_weapon'][row['TargetWeaponConditionId']])
                       if row['TargetWeaponConditionId'] else None)
            effect['weapon_correction']=dict(condition=condition,
                **{WEAPON_FIELDS[kind]:1 if kind in (100,101,116) else trait['trait_value']})
        else:
            if row['TargetWeaponConditionId']:
                raise NotImplementedError('Shield target-weapon condition routing not verified')
            # No active condition means neither weapon nor attack filter is
            # present: all three original builders set penetration affected false.
            effect['shield_correction']=dict(id=identity,IsPenetrationAffected=False,
                **{SHIELD_KINDS[kind]:trait['trait_value']})
        prepared[identity]=effect
    trial=deepcopy(unit)
    # Each original Execute installs its own correction before its first-call
    # side effect; later corrections must not change an earlier HP/EN callback.
    for trait in group['values']:
        effect=prepared[trait['runtime_trait_id']]
        trial.setdefault('runtime_trait_effects',{})[trait['runtime_trait_id']]=effect
        counter=effect.get('chance_step_counter')
        if counter is not None:
            trial['chance_step'][counter]=short(i32(trial['chance_step'][counter]+remaining_value(trait)))
        if effect.get('support_counter'):
            from .trait_count_effects import count_side_effect
            count_side_effect(trait,trial)
        if 'stat_correction' in effect:refresh_runtime_stats(trial)
        if trait['trait_type'] in RESOURCE_FIELDS:resource_on_execute(trait,trial)
    has_stats=any('stat_correction' in effect for effect in prepared.values())
    unit.setdefault('runtime_trait_effects',{}).update(prepared)
    for effect in prepared.values():
        if effect.get('chance_step_counter'):
            key=effect['chance_step_counter'];unit['chance_step'][key]=trial['chance_step'][key]
        if effect.get('support_counter'):
            key=effect['support_counter'];unit['support_budget'][key]=trial['support_budget'][key]
    if has_stats:
        for key in ('scalar_corrections','_runtime_stat_fields','_runtime_scalar_base_corrections'):
            unit[key]=trial[key]
        fields=set(trial['_runtime_stat_fields'])
        for stat in ('attack','defense','mobility'):
            if {stat.title()+'Rate',stat.title()+'Value'}&fields:unit[stat]=trial[stat]
        for stat,stem in (('hp','MaxHp'),('en','MaxEn')):
            if {stem+'Rate',stem+'Value'}&fields:
                unit['max_'+stat]=trial['max_'+stat];unit[stat]=trial[stat]
        if 'InitialMp' in fields:unit['mp']=trial['mp']
        for stem in ('Ranged','Melee','Defense','Reaction','Awaken'):
            if {'Character'+stem+'Rate','Character'+stem+'Value'}&fields:unit['pilot'][stem]=trial['pilot'][stem]
    group['effect_registration_complete']=True
    return list(prepared)


register_resolved_owner_weapon_set=register_resolved_owner_combat_set


def _can_remove(trait,unit):return True


def _on_remove(trait,unit):pass


def _remove_stat(trait,unit):
    refresh_runtime_stats(unit,excluding=trait['runtime_trait_id'])


def _remove_chance_step(trait,unit):
    """ChanceStepMaxCountChangeTrait.OnRemove: subtract remaining, not original limit."""
    counter='once' if trait['action_timing']==15 else 'guaranteed_once'
    unit['chance_step'][counter]=short(i32(unit['chance_step'][counter]-remaining_value(trait)))


def _remove_support(trait,unit):
    from .trait_count_effects import count_side_effect
    count_side_effect(trait,unit,removing=True)
