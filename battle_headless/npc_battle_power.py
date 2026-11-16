"""Fixed-stage NPC outgame rating inputs, separate from live combat buffs.

Only the pinned initial 90510005 NPC definitions are supported. Stage/map
buffs and supporter arguments are null in InitializeCalculator. Conversion
creates empty option-parts, SSP and custom-core lists for these NPCs.
"""
from .battle_power import RatingTrait,calculate_battle_power,weapon_rating
from .original import SOURCE_MASTER_SHA256,i32
from .trait_sources import npc_initial_sources,sort_abilities


def fixed_npc_power_inputs(stage):
    master=stage.master
    if stage.stage_id!=90510005 or master.sha256!=SOURCE_MASTER_SHA256:
        raise ValueError('NPC power binding requires pinned 90510005 master data')
    coefficients={r['Level']:r['CoefficientRatePercent'] for r in master.tables['m_weapon_enhancement_step_rating_point_coefficient']}
    configuration=[r for r in master.tables['m_config'] if r['ConfigTypeIndex']==153]
    coefficient=i32(int(configuration[0]['Value'])) if configuration else 100
    rows=[]
    for npc in stage.npcs:
        if len(npc['units'])!=1 or len(npc['characters'])!=1:
            raise NotImplementedError('NPC form/character selection requires original active-version binding')
        unit=npc['units'][0];character=npc['characters'][0];placement=npc['placement']
        if not placement['IsInitiallyPlaced'] or placement['MapNpcBuffId']:
            raise NotImplementedError('Initial NPC power binding does not support this placement/buff profile')
        u={field:i32(unit[column]) for field,column in [('hp','Hp'),('en','En'),('attack','Attack'),('defense','Defense'),('mobility','Mobility')]}
        c={field:i32(character[column]) for field,column in [('ranged','Ranged'),('melee','Melee'),('awaken','Awaken'),('defense','Defense'),('reaction','Reaction')]}
        if any(v<=0 for v in u.values()) or any(not 0<=v<=99999 for v in c.values()):
            raise NotImplementedError('NPC status clamp boundary needs a separate original binding')
        sources=npc_initial_sources(master,placement['Id'],unit,character);traits=[];trait_ids=[]
        for source in sources:
            for link in master.tables['m_trait_set']:
                if link['Id']!=source['master_trait_set_id']:continue
                trait=master.index['m_trait'][link['TraitId']]
                if trait['NeedAutoGenerateBattlePower'] or trait['TraitBoostConditionSetId']:
                    raise NotImplementedError('Generated/conditional rating requires original trait boost binding')
                trait_ids.append(trait['Id'])
                traits.append(dict(kind=trait['TraitTypeIndex'],rating=trait['BattlePowerValueRatePercent'],boost_count=1))
        weapons=[]
        for row in sort_abilities(unit['weapons']):
            definition=master.index['m_weapon'][row['WeaponId']]
            if definition['WeaponTypeIndex']!=1:raise NotImplementedError('NPC MAP/special rating weapon binding is not established')
            status=master.index['m_weapon_status'][definition['WeaponStatusId']]
            attributes=[r['AttackAttributeTypeIndex'] for r in master.tables['m_attack_attribute_set']
                if r['AttackAttributeSetId']==definition['AttackAttributeSetId']]
            weapons.append(dict(id=row['WeaponId'],attributes=attributes,
                base_rating=status['BattlePowerRatingPointRatePercent'],core_rating=0,level=1))
        rows.append(dict(npc_id=placement['Id'],side=placement['BattleSideTypeIndex'],
            adv_only=any(r['IsExclusiveAdv'] for r in npc['setting']),trait_ids=trait_ids,sources=sources,
            input=dict(unit=u,character=c,traits=traits,weapons=weapons,coefficient=coefficient,level_coefficients=coefficients)))
    return rows


def fixed_npc_battle_powers(stage):
    result=[]
    for row in fixed_npc_power_inputs(stage):
        case=row['input'];weapons=[]
        for weapon in case['weapons']:
            rating=weapon_rating(weapon['base_rating'],case['level_coefficients'].get(weapon['level'],0),weapon['core_rating'])
            minimum=weapon_rating(weapon['base_rating'],case['level_coefficients'].get(1,0),weapon['core_rating'])
            weapons.append(dict(id=weapon['id'],attributes=weapon['attributes'],rating=rating,level_up_rating=rating-minimum))
        power=calculate_battle_power(case['unit'],case['character'],weapons,
            [RatingTrait(**t) for t in case['traits']],case['coefficient'])
        result.append(dict(row,output=power,weapon_projections=weapons))
    return result
