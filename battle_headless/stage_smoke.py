"""Run a real HARD stage fixture under an explicitly controlled no-traits profile."""
from .tension import current_tension
from .unit_life import is_alive
from copy import deepcopy
import json
from .engine import BattleEnv
from .original import OriginalScalarKernel
from .random_original import OriginalRandom
from .stage import StageFixture
from .combat_primitives import character_attack_value


class FixtureUnits:
    def __init__(self, stage, units):
        self.sha256 = stage.master.sha256
        self.units = units

    def unit(self, unit_id, instance, team, position):
        result = deepcopy(self.units[instance])
        if result['unit_id']!=unit_id or result['team']!=team or result['position']!=position:
            raise ValueError('Fixture unit reference mismatch')
        return result


class ControlledRules:
    name = 'original-scalar-and-mt19937-no-traits'

    def __init__(self, master):
        self.master=master
        self.kernel = OriginalScalarKernel(master)
        self.coefficients = {r['IngameCoefficientConfigTypeIndex']:r['Value']
                             for r in master.tables['m_ingame_coefficient_config']}

    def resolve_attack(self, attacker, target, weapon, rng):
        return self._calculate_attack(attacker,target,weapon,rng,prediction=False)

    def predict_attack(self,attacker,target,weapon):
        return self._calculate_attack(attacker,target,weapon,None,prediction=True)

    def predict_counter(self,attacker,target,weapon):
        return self._calculate_attack(attacker,target,weapon,None,prediction=True,
            action_type='Counter',opponent_action_type='Attack')

    def resolve_counter(self,attacker,target,weapon,rng):
        return self._calculate_attack(attacker,target,weapon,rng,prediction=False,
            action_type='Counter',opponent_action_type='Attack')

    def _calculate_attack(self,attacker,target,weapon,rng,*,prediction,
                          action_type='Attack',opponent_action_type='NoAction',geometry_target=None):
        from .weapon_corrections import select_combat_corrections,sum_shields,with_combat_stats
        from .battle_evaluation import HitRate,compose_hit_rate,evaluate_hit,evaluate_critical,evaluate_parry
        if weapon.get('_combat_context')!=(action_type,opponent_action_type):
            weapon=self.effective_weapon(attacker,target,weapon,action_type=action_type,
                opponent_action_type=opponent_action_type)
        target_selection=select_combat_corrections(self.master,target,attacker,weapon,
            action_type=opponent_action_type,opponent_action_type=action_type,weapon_owner_id=attacker['id'])
        attacker=with_combat_stats(attacker,weapon['_combat_selection'])
        target=with_combat_stats(target,target_selection)
        attack = character_attack_value(attacker['pilot'],weapon.get('attack_attributes',[weapon.get('attack_attribute')]))
        target_pilot = target['pilot']
        counter_type = (2 if opponent_action_type == 'Evasion' else
                        3 if opponent_action_type in ('SupportDefense', 'Defense') else
                        weapon.get('_target_counter_type', 0))
        from .weapon_passives import corrections as passive_corrections
        from .original import i32
        passive=passive_corrections(self.master,attacker,target,weapon,action_type,opponent_action_type)
        from .weapon_power_boosts import effective_power
        weapon=dict(weapon)
        power_records=weapon['_combat_selection']['weapon']+passive['weapon']
        weapon['power']=self.weapon_power(attacker,geometry_target if geometry_target is not None else target,weapon.get('_base_weapon',weapon),power_records)
        given=dict(attacker.get('scalar_corrections',{}))
        for field,value in passive['values'].items():given[field]=i32(given.get(field,0)+value)
        taken=target.get('scalar_corrections',{})
        from .attack_target_stats import reduced_target_stat
        target_mobility=reduced_target_stat(target,'mobility',given.get('AttackTargetMobilityReductionRate',0))
        target_defense=reduced_target_stat(target,'defense',given.get('AttackTargetDefenseReductionRate',0))
        hit_spec=compose_hit_rate(HitRate(weapon['hit'],weapon.get('sure_hit',False),weapon.get('fixed_hit_rate',False)),
            target_action_inability=taken.get('ActionInabilityCount',0)>0,
            absolute_evasion=taken.get('AbsoluteEvasionCount',0)>0,
            attacker_sure_hit=given.get('SureHitAttackCount',0)>0,
            mobility_correction=self.kernel.mobility_correction(attacker['mobility'],attack,target_mobility,target_pilot['Reaction']),
            ability_correction=-taken.get('EvasionRate',0),counter_type=counter_type)
        # Capture only actual draws. Short circuits must not reuse last_roll.
        draws=[];evaluations=[]
        class Evaluator:
            def evaluate(self,rate):
                result=rng.evaluate(rate);entry=dict(rate=rate,roll=rng.last_roll)
                evaluations.append(entry)
                if rng.last_roll is not None:draws.append(entry)
                return result
        evaluator=Evaluator()
        hit_rate=hit_spec.value
        before=len(draws)
        hit=evaluate_hit(hit_rate,evaluator,sure_hit=hit_spec.sure_hit,prediction=prediction,
            target_is_broken=weapon.get('_target_is_broken',False))
        hit_roll=draws[-1]['roll'] if len(draws)>before else None
        parry=False;parry_roll=None
        if hit and not hit_spec.sure_hit and not hit_spec.fixed and taken.get('ActionInabilityCount',0)<=0:
            before=len(draws)
            parry=evaluate_parry(taken.get('ParryRate',0),evaluator,prediction=prediction)
            parry_roll=draws[-1]['roll'] if len(draws)>before else None
        tension = current_tension(attacker,self.kernel)
        before=len(draws)
        critical_type=evaluate_critical(weapon['critical'],tension,evaluator,
            guaranteed=given.get('GuaranteedCriticalCount',0)>0,prediction=prediction)
        critical=critical_type!=0
        critical_roll=draws[-1]['roll'] if len(draws)>before else None
        tension_rate = self.coefficients.get({3:30,4:31,5:32}.get(tension),0)
        defense_multiplier = self.kernel.defense_multiplier(counter_type,
            trait_reduction=sum_shields(target_selection['shields'],'DefensiveReductionRate'))
        critical_bonus = self.coefficients[{2:33,3:34,4:35,5:36}[tension]] if critical else 0
        battle = self.kernel.battle_damage(weapon['power'],attacker['attack'],target_defense,
                                           attack,target_pilot['Defense'],weapon_efficiency=weapon['efficiency'])
        from .damage_caps import damage_cap
        damage = self.kernel.final_damage(battle,tension_rate=tension_rate,
            given_rate=given.get('DamageGivenCorrectionRate',0),given_flat=given.get('DamageGivenCorrectionValue',0),
            critical_rate=given.get('CriticalDamageGivenCorrectionRate',0) if critical else 0,
            critical_flat=given.get('CriticalDamageGivenCorrectionValue',0) if critical else 0,
            taken_rate=sum_shields(target_selection['shields'],'ReductionRate'),
            taken_flat=sum_shields(target_selection['shields'],'ReductionValue'),
            negate_threshold=taken.get('DamageNegationValue',0),critical_bonus=critical_bonus,
            defense_multiplier=defense_multiplier,
            upper_limit=damage_cap(target.get('damage_taken_upper_limit_values',[]))) if hit and not parry else 0
        from .hp_barrier import split_damage
        hp_damage,hp_barrier_damage=split_damage(target,weapon,damage)
        from .weapon_drain import first_drain
        from .weapon_resource_damage import en_attack_value,mp_attack_value,mp_change_enabled
        traits=weapon.get('weapon_traits',[])
        en_value=en_attack_value(traits,target['max_en'])
        mp_value=mp_attack_value(traits) if mp_change_enabled(target) else None
        en_damage=en_value if hit and not parry and en_value is not None and en_value>0 else None
        mp_damage=mp_value if hit and not parry and mp_value is not None and mp_value>0 else None
        drained_en=first_drain(traits,11,en_value or 0,attacker['max_en']) if hit and not parry else None
        drained_hp=first_drain(weapon.get('weapon_traits',[]),10,damage,attacker['max_hp'])
        from .weapon_debuffs import prepare_debuffs
        debuffs=prepare_debuffs(self.master,attacker,target,weapon,action_type,opponent_action_type)
        return dict(hit=hit,critical=critical,damage=damage,hit_rate=hit_rate,drained_hp=drained_hp,drained_en=drained_en,
                    hp_damage=hp_damage,hp_barrier_damage=hp_barrier_damage,
                    en_damage=en_damage,mp_damage=mp_damage,
                    hit_roll=hit_roll,critical_roll=critical_roll,
                    critical_type=critical_type,parry=parry,parry_roll=parry_roll,prediction=prediction,
                    attacker_attack=attacker['attack'],attack_target_defense=target_defense,attack_target_mobility=target_mobility,
                    defense_multiplier=defense_multiplier,
                    random_draws=draws,random_evaluations=evaluations,
                    critical_rate=weapon['critical'],battle_damage=battle,
                    weapon_passives=passive,weapon_debuffs=debuffs,weapon_power=weapon['power'],
                    geometry_target=(geometry_target if geometry_target is not None else target)['id'],
                    attacker_combat_traits=weapon['_combat_selection']['applied'],
                    target_combat_traits=target_selection['applied'],en_cost=weapon['en'],
                    profile=self.name)

    def effective_weapon(self,unit,target,weapon,*,action_type='Attack',opponent_action_type='NoAction',geometry_target=None):
        from .weapon_corrections import (select_combat_corrections,sum_field,en_consumption,
            percentage_delta,mp_consumption,hp_consumption_rate,hp_consumption,normal_max_range)
        from .original import i32
        weapon=weapon.get('_base_weapon',weapon)
        selection=select_combat_corrections(self.master,unit,target,weapon,
            action_type=action_type,opponent_action_type=opponent_action_type)
        records=selection['weapon'];result=dict(weapon)
        result['preemptive'] = (bool(weapon.get('preemptive', False)) or
            13 in weapon.get('weapon_trait_types', []) or
            sum_field(records,'PreemptiveAttackAdditionCount',weapon)>0)
        # UnitWeapon._IsSureHitAttack / _IsFixedHitRate query native trait types 5 / 15.
        result['sure_hit'] = bool(weapon.get('sure_hit',False) or
                                  5 in weapon.get('weapon_trait_types', []))
        result['fixed_hit_rate'] = bool(weapon.get('fixed_hit_rate',False) or
                                        15 in weapon.get('weapon_trait_types', []))
        result['hit']=i32(weapon['hit']+(0 if result['fixed_hit_rate'] else
                                       sum_field(records,'HitRate',weapon)))
        result['critical']=i32(weapon['critical']+sum_field(records,'CriticalRate',weapon))
        result['range_max']=normal_max_range(weapon['range_min'],weapon['range_max'],
            sum_field(records,'MaxRangeValue',weapon))
        result['en']=en_consumption(weapon['en'],records,weapon)
        result['mp_cost']=mp_consumption(weapon.get('base_mp_consumption',0),records,weapon)
        rate=hp_consumption_rate(weapon.get('base_hp_consumption_rate',0),records,weapon)
        result['hp_cost']=hp_consumption(unit['max_hp'],rate)
        from .weapon_power_boosts import effective_power
        result['power']=self.weapon_power(unit,target,weapon,records)
        result['_combat_selection']=selection
        result['_combat_context']=(action_type,opponent_action_type)
        result['_base_weapon']=weapon
        return result

    def weapon_power(self,unit,target,base,records):
        from .weapon_power_boosts import effective_power
        from .weapon_corrections import sum_field,normal_max_range
        if target is None or not (sum_field(records,'ProximityValue',base) or sum_field(records,'RemotenessValue',base)):
            return effective_power(unit,base,records)
        distance=(self._battle_distance(unit,target) if hasattr(self,'_battle_distance') else
                  sum(abs(a-b) for a,b in zip(unit['position'],target['position'])))
        # Native _GetWeapons(false,false) does not filter low-resource weapons.
        weapons=unit.get('weapons') or [base]
        extreme=max(normal_max_range(w['range_min'],w['range_max'],
                         sum_field(records,'MaxRangeValue',w)) for w in weapons)
        return effective_power(unit,base,records,distance=distance,extreme_max=extreme)

    def weapon_available(self, unit, target, weapon, *, remaining_hp=None):
        from .weapon_availability import WeaponAvailabilityInputs,unavailable_reasons
        if '_combat_selection' not in weapon:weapon=self.effective_weapon(unit,target,weapon)
        corrections=unit.get('scalar_corrections',{})
        # This runner's normalized weapons and map are already space-capable.
        # Geometry remains in BattleEnv.legal_actions; no target geometry adapter
        # is silently substituted for the original occupied-area service here.
        state=WeaponAvailabilityInputs(remaining_hp=unit['hp'] if remaining_hp is None else remaining_hp,en=unit['en'],
            en_consumption=weapon['en'],mp=unit['mp'],mp_consumption=weapon['mp_cost'],
            tension=current_tension(unit,self.kernel),required_tension=weapon.get('required_tension',0),
            hp_consumption=weapon['hp_cost'] or 0,has_capability=True,
            action_inability=corrections.get('ActionInabilityCount',0)>0,
            attack_inability=corrections.get('AttackInabilityCount',0)>0)
        return not unavailable_reasons(state)

    def consume_weapon(self,unit,weapon):
        from .weapon_use import use_weapon
        return use_weapon(unit,weapon['en'],weapon['mp_cost'],weapon['hp_cost'])

    def reflect_battle_mp(self,unit):
        # Current executor has one attack per battle. The full counter/support
        # executor must keep this boundary after all participant weapon uses.
        from .weapon_use import reflect_mp_change
        reflect_mp_change(unit)


def build_env(stage=None, seed=37):
    stage = stage or StageFixture()
    master = stage.master
    def convert(npc, instance, team, position=None):
        if len(npc['units'])!=1 or len(npc['characters'])!=1:
            raise NotImplementedError('Smoke profile requires one unit form and one pilot')
        source, pilot, placement = npc['units'][0],npc['characters'][0],npc['placement']
        weapons=[]
        for w in source['weapons']:
            spec = master.index['m_weapon'][w['WeaponId']]
            if spec['WeaponTypeIndex']==3:
                raise NotImplementedError('MAP weapons require a dedicated executor')
            attrs = [r['AttackAttributeTypeIndex'] for r in master.tables['m_attack_attribute_set']
                     if r['AttackAttributeSetId']==spec['AttackAttributeSetId']]
            if len(attrs)!=1:
                raise NotImplementedError('Multiple attack attributes require original aggregation')
            cap = master.index['m_weapon_capability_set'][spec['WeaponCapabilitySetId']]
            if not cap['HasCapabilityInSpace']:
                continue
            weapons.append(dict(id=w['WeaponId'],power=w['Power'],en=w['En'],hit=w['HitRate'],
                weapon_traits=[deepcopy(master.index['m_weapon_trait'][link['WeaponTraitId']])
                    for link in master.tables['m_map_npc_unit_weapon_trait_set_content']
                    if link['MapNpcWeaponTraitSetId']==w['MapNpcWeaponTraitSetId']],
                weapon_trait_types=[master.index['m_weapon_trait'][link['WeaponTraitId']]['WeaponTraitTypeIndex']
                    for link in master.tables['m_map_npc_unit_weapon_trait_set_content']
                    if link['MapNpcWeaponTraitSetId']==w['MapNpcWeaponTraitSetId']],
                base_mp_consumption=w['MpConsumptionValue'],base_hp_consumption_rate=w['HpConsumptionRate'],
                required_tension=spec['TensionTypeIndex'],sort_order=w['SortOrder'],
                critical=w['CriticalRate'],range_min=w['RangeMin'],range_max=w['RangeMax'],
                attack_attribute=attrs[0],efficiency=cap['DamageEfficiencyInSpace']/100,
                attack_attributes=attrs,
                weapon_attributes=[r['WeaponAttributeTypeIndex'] for r in master.tables['m_weapon_attribute_set']
                                   if r['WeaponAttributeSetId']==spec['WeaponAttributeSetId']],
                source=deepcopy(w)))
        return dict(id=instance,unit_id=source['UnitId'],team=team,
            position=list(position or [placement['X'],placement['Y']]),
            hp=source['Hp'],max_hp=source['Hp'],en=source['En'],max_en=source['En'],
            attack=source['Attack'],defense=source['Defense'],mobility=source['Mobility'],
            movement=source['Movement'],pilot=deepcopy(pilot),mp=pilot['Mp'],
            weapons=weapons,moved=False,acted=False,source_npc_id=placement['Id'])
    units = {}
    # Test formation is explicitly a mirrored stage NPC; no user roster was supplied.
    reference = next(n for n in stage.npcs if n['units'][0]['UnitId']==1001000100)
    anchor = stage.source['m_map_stage_group_initial_placement'][0]
    player = convert(reference,'test_player','player',[anchor['X'],anchor['Y']])
    units[player['id']] = player
    for npc in stage.npcs:
        if not npc['placement']['IsInitiallyPlaced']:
            continue
        if npc['placement']['BattleSideTypeIndex']!=2:
            raise NotImplementedError('Smoke profile requires original enemy side 2')
        unit = convert(npc,str(npc['placement']['Id']),'enemy')
        units[unit['id']] = unit
    blocked=[]
    for y in range(stage.height):
        for x in range(stage.width):
            terrain = stage.terrain.get((x,y),[])
            if not terrain or all(t['TerrainTypeIndex']!=2 for t in terrain):
                raise NotImplementedError('Movement smoke profile requires a fully specified space map')
    scenario = dict(width=stage.width,height=stage.height,max_turns=6,blocked=blocked,
        units=[dict(id=u['id'],unit_id=u['unit_id'],team=u['team'],position=u['position']) for u in units.values()])
    env = BattleEnv(FixtureUnits(stage,units),ControlledRules(master))
    env.reset(scenario,seed)
    env.rng = OriginalRandom(seed)
    return env,stage


def main():
    import argparse
    from pathlib import Path
    from battle_paths import OUTPUTS
    from .__main__ import choose
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage',type=int,default=90510005)
    p.add_argument('--seed',type=int,default=37)
    p.add_argument('--mechanics',action='store_true',help='Apply verified unconditional NPC owner passives; support execution is still pending')
    p.add_argument('--out',type=Path,default=OUTPUTS/'battle_headless/eternal_road_hard')
    args=p.parse_args()
    stage=StageFixture(stage_id=args.stage)
    args.out.mkdir(parents=True,exist_ok=True)
    (args.out/'stage.json').write_text(json.dumps(stage.package(),ensure_ascii=False,indent=2),encoding='utf-8')
    env,stage=build_env(stage,args.seed)
    passive_summary=[]
    if args.mechanics:
        from .mechanics import attach_stage_passives
        passive_summary=attach_stage_passives(env,stage)
    while not (env.terminated or env.truncated):
        env.step(choose(env))
    report=env.replay()
    report.update(stage_id=stage.stage_id,name=stage.name,difficulty='Hard',
        original_stage_conditions=stage.conditions,
        conditions_at_supplied_turn3=stage.evaluate_conditions(
            {0:sum(is_alive(u) for u in env.units.values() if u['team']=='player'),
             2:sum(is_alive(u) for u in env.units.values() if u['team']=='enemy')},3),
        passive_summary=passive_summary,
        exact_gameplay=False,unsupported=['Conditional/timed passives and weapon traits not applied' if args.mechanics else 'NPC abilities and weapon traits not applied',
            'No counters/support/skills/MP event changes',
            'Phase clock uses experimental six-phase test schedule',
            'Test player is a mirrored stage NPC, not an account formation',
            'Flat-grid movement is the test scheduler, not a fully ported movement service'])
    (args.out/'smoke_replay.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(stage_id=stage.stage_id,name=stage.name,width=stage.width,height=stage.height,
        enemies=len(stage.npcs),actions=len(env.events),attacks=sum(e['action']['type']=='attack' for e in env.events),
        winner=env.winner,profile=env.rules.name,exact_gameplay=False),ensure_ascii=False))


if __name__=='__main__':
    main()
