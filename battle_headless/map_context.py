"""Original MAP context from normalized units and registered trait subsets.

Unambiguous stage terrain can be bound automatically. Mixed terrain selection,
dynamic chip effects and unported trait sources require their original services.
"""
from .original import i32,div,round_up
from .mechanics import short

TERRAIN_FIELDS={2:'Space',3:'Atmospheric',4:'Ground',5:'Surface',6:'Underwater'}


def power_resources(unit):
    """GetWeaponPower(MAP) 0x613e428 uses battle-start HP/EN, current MP.

    Nullable snapshot getters fall back to current values outside a battle.
    This projects inputs only; battle lifecycle owns snapshot creation/clear.
    """
    result=dict(unit)
    for field in ('hp','en'):
        captured=unit.get('battle_start_'+field)
        if captured is not None:result[field]=captured
    return result


def damage_efficiency(capability,terrain_type):
    name=TERRAIN_FIELDS.get(i32(terrain_type))
    return div(short(capability['DamageEfficiencyIn'+name]),100) if name else 0.0


def terrain_hit_correction(values):
    values=list(values)
    if not values:return 0
    total=0
    for value in values:total=i32(total+value)
    return round_up(div(total,len(values)))


def target_terrain(env,target):
    explicit=target.get('standby_terrain_type')
    if 'map_terrain_data' in target:
        if explicit is None:raise NotImplementedError('Runtime terrain rows require original standby terrain type')
        return explicit,[row['evasion_correction_percent'] for row in target['map_terrain_data']]
    stage=getattr(env,'_map_context_stage',None)
    if stage is None:raise NotImplementedError('Original MAP stage/terrain service is not bound')
    coords=env.battle_map.ordered_occupied(target)
    if explicit is None:
        types=None
        for coord in coords:
            current={r['TerrainTypeIndex'] for r in stage.terrain.get(coord,[])}
            types=current if types is None else types&current
        if not types or len(types)!=1:raise NotImplementedError('Mixed/unknown standby terrain requires original unit terrain selection')
        explicit=next(iter(types))
    values=[]
    for coord in coords:
        if not stage.in_bounds(coord):continue
        rows=[r for r in stage.terrain.get(coord,[]) if r['TerrainTypeIndex']==explicit]
        if len(rows)>1:raise NotImplementedError('Overlapping terrain motif priority is not imported')
        if rows and rows[0]['DefaultEvasionRatePercentage']:
            raise NotImplementedError('Import resolved MapChipTerrainData for nonzero/dynamic evasion effects')
        # FindUnitExpandTerrainDataList inserts a zero row if a chip exists but
        # lacks the unit's standby terrain. Exclusion/open states do not delete chips.
        values.append(0)
    return explicit,values


def build_context(env,attacker,target,weapon,row,prediction):
    from .map_weapon_data import properties
    from .weapon_corrections import select_combat_corrections,sum_shields
    from .weapon_passives import corrections
    from .combat_primitives import character_attack_value
    from .attack_target_stats import reduced_target_stat
    from .battle_evaluation import compose_hit_rate
    from .weapon_resource_damage import en_attack_value,mp_attack_value
    from .damage_caps import damage_cap
    if 'map_level_stats' not in weapon:raise NotImplementedError('Original MAP level definition required for automatic context')
    terrain,terrain_values=target_terrain(env,target)
    master=env.rules.master;kernel=env.rules.kernel
    attack_selection=select_combat_corrections(master,attacker,target,weapon,'Attack','NoAction',attacker['id'])
    target_selection=select_combat_corrections(master,target,attacker,weapon,'NoAction','Attack',attacker['id'])
    if attack_selection['scalar'] or target_selection['scalar']:
        raise NotImplementedError('Conditional MAP scalar activation requires its original participant lifecycle')
    passive=corrections(master,attacker,target,weapon,'Attack','NoAction')
    effective=properties(power_resources(attacker),weapon,attack_selection['weapon']+passive['weapon'])
    given=dict(attacker.get('scalar_corrections',{}));taken=target.get('scalar_corrections',{})
    for field,value in passive['values'].items():given[field]=i32(given.get(field,0)+value)
    attack=character_attack_value(attacker['pilot'],weapon['attack_attributes'])
    mobility=reduced_target_stat(target,'mobility',given.get('AttackTargetMobilityReductionRate',0))
    defense=reduced_target_stat(target,'defense',given.get('AttackTargetDefenseReductionRate',0))
    hit=compose_hit_rate(effective['hit_rate_spec'],target_action_inability=taken.get('ActionInabilityCount',0)>0,
        absolute_evasion=taken.get('AbsoluteEvasionCount',0)>0,attacker_sure_hit=given.get('SureHitAttackCount',0)>0,
        mobility_correction=kernel.mobility_correction(attacker['mobility'],attack,mobility,target['pilot']['Reaction']),
        ability_correction=i32(-taken.get('EvasionRate',0)),map_effect=terrain_hit_correction(terrain_values),counter_type=0)
    from .tension import current_tension
    tension=current_tension(attacker,kernel)
    coefficients={r['IngameCoefficientConfigTypeIndex']:r['Value'] for r in master.tables['m_ingame_coefficient_config']}
    context=dict(hit_rate=hit,critical_rate=effective['critical'],tension=tension,
        guaranteed_critical=given.get('GuaranteedCriticalCount',0)>0,
        damage_inputs=dict(power=effective['power'],attack=attacker['attack'],defense=defense,character_attack=attack,
            character_defense=target['pilot']['Defense'],weapon_efficiency=damage_efficiency(weapon['capability'],terrain)),
        final_inputs=dict(given_rate=given.get('DamageGivenCorrectionRate',0),given_flat=given.get('DamageGivenCorrectionValue',0),
            critical_rate=given.get('CriticalDamageGivenCorrectionRate',0),critical_flat=given.get('CriticalDamageGivenCorrectionValue',0),
            tension_rate=coefficients.get({3:30,4:31,5:32}.get(tension),0),critical_bonus=coefficients[{2:33,3:34,4:35,5:36}[tension]],
            taken_rate=sum_shields(target_selection['shields'],'ReductionRate'),taken_flat=sum_shields(target_selection['shields'],'ReductionValue'),
            negate_threshold=taken.get('DamageNegationValue',0),upper_limit=damage_cap(target.get('damage_taken_upper_limit_values',[])),
            damage_cut=bool(target.get('damage_cut',False)),defense_multiplier=1.0),
        en_attack_value=en_attack_value(weapon['weapon_traits'],target['max_en']),mp_attack_value=mp_attack_value(weapon['weapon_traits']),
        trace=dict(profile='registered-traits-and-bound-terrain-subset',terrain_type=terrain,terrain_hit_correction=terrain_hit_correction(terrain_values),
            attack_corrections=attack_selection['applied'],target_corrections=target_selection['applied'],
            weapon_passives=passive['applied'],pending_weapon_passives=passive['pending']))
    if weapon.get('weapon_trait_addition'):
        raise NotImplementedError('Full MAP weapon-trait damage additions are not imported')
    return context
