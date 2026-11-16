"""Ordinary calculator binding over the fixed stage's shared trait repository.

This composes the recovered calculator with imported units, explicit map/turn
inputs and the concrete initial/weapon runtime. It does not schedule attacks,
apply damage, grant selected debuffs or construct a prediction repository.
"""
from copy import deepcopy
from dataclasses import dataclass
from .original import OriginalScalarKernel, i32
from .battle_calculation import calculate_battle_result
from .battle_calculation_result import BattleCalculationResult, HitResult
from .battle_evaluation import HitRate, compose_hit_rate, evaluate_hit, evaluate_parry, evaluate_critical
from .weapon_corrections import sum_field, sum_shields, en_consumption, mp_consumption, hp_consumption_rate, hp_consumption, normal_max_range, truncdiv
from .weapon_power_boosts import effective_power
from .weapon_resource_damage import en_attack_value, mp_attack_value, mp_change_enabled
from .weapon_drain import first_drain
from .unit_mechanisms import can_escape_without_mechanism
from .trait_context import build_trait_context
from .trait_activation import weapon_trait_condition_matches
from .weapon_passive_lifecycle import FixedStageWeaponPassiveRuntime
from .combat_primitives import character_attack_value
from .attack_target_stats import reduced_target_stat
from .tension import current_tension
from .geometry import place_set_distance
from .map_context import damage_efficiency, terrain_hit_correction, TERRAIN_FIELDS
from .mechanics import short


def weapon_records(unit):
    return [effect['weapon_correction'] for effect in unit['runtime_trait_effects'].values()
            if 'weapon_correction' in effect]


def shields(unit):
    return [effect['shield_correction'] for effect in unit['runtime_trait_effects'].values()
            if 'shield_correction' in effect]


def correction_damage_percentage(given,weapon,tension,taken,efficiency):
    """CalcCorrectionDamageRate's Int32 product and truncating percentage."""
    rate=i32(i32(i32(given+weapon)+tension)-taken)
    return i32(efficiency+truncdiv(i32(rate*efficiency),100)-100)


def unit_mp_consumption(cost,enabled):
    """GetWeaponMpConsumption preserves absent versus present zero."""
    return None if cost is None else i32(cost) if enabled else 0


@dataclass(frozen=True)
class BoundUnitWeapon:
    base: dict
    corrections: tuple
    capability: dict

    def hit_rate(self):
        kinds={row['WeaponTraitTypeIndex'] for row in self.base['weapon_traits']}
        fixed=15 in kinds
        return HitRate(i32(self.base['hit']+(0 if fixed else sum_field(self.corrections,'HitRate',self.base))),5 in kinds,fixed)

    def critical_rate(self):
        return i32(self.base['critical']+sum_field(self.corrections,'CriticalRate',self.base))

    def efficiency_percent(self,terrain):
        name=TERRAIN_FIELDS.get(i32(terrain))
        return short(self.capability['DamageEfficiencyIn'+name]) if name else 0


@dataclass(frozen=True)
class BoundDamageContainer:
    attacker: dict
    weapon: BoundUnitWeapon
    target: dict
    counter_type: int
    terrain: int


class FixedStageBattleCalculationServices:
    def __init__(self,runtime,factory,*,map_root,terrain_rows,context_origin,rng,phase,step,
                 show_tutorial_support_defend,source_name):
        self.runtime=runtime;self.master=runtime.master;self.kernel=OriginalScalarKernel(self.master)
        self.map=map_root;self._terrain_rows=terrain_rows;self._context_origin=context_origin
        self.rng=rng;self.phase=phase;self.step=step;self.tutorial=show_tutorial_support_defend
        self.passives=FixedStageWeaponPassiveRuntime(runtime,factory,source_name=source_name)
        self.refresh_units=[];self.results={};self.events=[];self._weapon=None
        self.coefficients={r['IngameCoefficientConfigTypeIndex']:r['Value']
                           for r in self.master.tables['m_ingame_coefficient_config']}

    def context(self,participation):
        return build_trait_context(self.runtime.units.values(),turn=self.phase.turn,step=self.step,
            map_origin=self._context_origin,participation=participation)

    def calculate(self,attacker,attack_coord,weapon_id,main_target,target_coord,counter_type,
                  guard=None,participation=None,*,keep_weapon_traits=False,prediction=False):
        if prediction:raise NotImplementedError('Create and bind the original independent prediction repository first')
        for unit in (attacker,main_target,guard):
            if unit is not None and self.runtime.units.get(unit['id']) is not unit:
                raise ValueError('Calculator participants must be canonical shared repository units')
        # No legacy temporary passives or unresolved terrain are silently mixed
        # into this repository. Check all participants before enabling sources.
        for unit in (attacker,main_target,guard):
            if unit is None:continue
            if unit.get('combat_traits') or unit.get('active_skill_effects') or unit.get('active_weapon_debuffs'):
                raise NotImplementedError('Register dynamic sources in the shared trait repository first')
            unit['standby_terrain_type']
        self.events=[]
        return calculate_battle_result(self,attacker,attack_coord,weapon_id,main_target,target_coord,
            counter_type,guard,participation,keep_weapon_traits=keep_weapon_traits,prediction=False)

    def map_root(self):return self.map
    def can_escape(self,unit):return can_escape_without_mechanism(self.master,unit)
    def is_unbreakable(self,unit):return i32(unit['scalar_corrections'].get('UnbreakableCount',0))>0
    def is_broken(self,unit):return not self.is_unbreakable(unit) and i32(unit['hp'])<=0
    def unit_id(self,unit):return unit['id']
    def max_hp(self,unit):return i32(unit['max_hp'])
    def max_en(self,unit):return i32(unit['max_en'])
    def current_hp(self,unit):return i32(unit['hp'])
    def is_action_inability(self,unit):return i32(unit['scalar_corrections'].get('ActionInabilityCount',0))>0
    def is_attack_inability(self,unit):return i32(unit['scalar_corrections'].get('AttackInabilityCount',0))>0
    def standby_terrain(self,unit):return unit['standby_terrain_type']
    def current_turn(self):return self.phase.turn
    def current_step(self,turn):return self.step
    def show_tutorial_support_defend(self,step):return self.tutorial
    def damage_config(self):return self.kernel.config
    def make_hit_rate(self,value,sure,fixed):return HitRate(value,sure,fixed)
    def hit_rate_value(self,rate):return rate.value
    def hit_is_sure(self,rate):return rate.sure_hit
    def hit_is_fixed(self,rate):return rate.fixed
    def hit_result(self,hit,parry,rate):return HitResult.create(hit,parry,rate)
    def battle_result(self,**fields):return BattleCalculationResult(**fields)
    def inability_result(self,weapon,action,attack):return BattleCalculationResult.inability(weapon,action,attack)
    def save_result(self,identity,result):self.results[identity]=result
    def critical_type(self,value):return value['kind']
    def mp_change_enabled(self,unit):return mp_change_enabled(unit)

    def get_weapon(self,unit,identity):
        base=next((w for w in unit['weapons'] if w['id']==identity and identity not in unit.get('disabled_weapon_ids',())),None)
        if base is None:raise KeyError(f'No current ordinary weapon {identity}')
        # Fixed roster uses only passive/sure-hit/debuff traits. Extra recovered
        # scalar resource kinds are also safe here; penetration needs its getter.
        kinds={row['WeaponTraitTypeIndex'] for row in base['weapon_traits']}
        if kinds-{3,5,8,9,10,11,12,13,14,15}:
            raise NotImplementedError('Resolve additional ordinary weapon behavior before binding')
        spec=self.master.index['m_weapon'][identity]
        capability=self.master.index['m_weapon_capability_set'][spec['WeaponCapabilitySetId']]
        result=BoundUnitWeapon(base,tuple(deepcopy(weapon_records(unit))),capability)
        self._weapon=result
        self.events.append(dict(event='get_weapon',unit=unit['id'],weapon=identity,corrections=deepcopy(result.corrections)))
        return result

    def expand_unit_coord(self,unit,coord,direction):
        if direction!=0:raise NotImplementedError('Calculator expects original unspecified direction')
        return self.map.occupied(unit,coord)

    def min_distance(self,left,right):return place_set_distance(left,right)
    def calc_distance(self,actor,coord,target,target_coord):
        return self.min_distance(self.expand_unit_coord(actor,coord,0),self.expand_unit_coord(target,target_coord,0))
    def set_active_attacker(self,participation,*args):participation.set_active_attacker(*args)
    def weapon_traits(self,weapon):return weapon.base['weapon_traits']

    def enable_weapon_passives(self,identity,traits,participation,prediction):
        if prediction:raise NotImplementedError('Independent prediction repository required')
        if traits is not self._weapon.base['weapon_traits']:raise ValueError('Weapon trait source mismatch')
        applied=self.passives.enable(identity,self._weapon.base,participation,self.context,self.rng,self.phase)
        self.events.append(dict(event='enable_weapon_passives',source_ids=sorted(applied)))
        return applied

    def execute_always(self,units,participation):
        result=self.runtime.execute(self.context,participation,weapon=self._weapon.base,units=units)
        self.events.append(dict(event='always',unit_ids=[u['id'] for u in units],effects=result['effects']))

    def disable_weapon_traits(self,units):
        self.passives.disable(units)
        self.events.append(dict(event='disable_weapon_passives',unit_ids=[u['id'] for u in units]))

    def evaluate_weapon_debuffs(self,identity,traits,participation):
        context=self.context(participation);result=set()
        for source in traits:
            if source is None or source['WeaponTraitTypeIndex']!=12:continue
            if not weapon_trait_condition_matches(self.master,self.runtime.units,self.runtime.non_unit_owners,
                    identity,source,context,participation,self._weapon.base):continue
            if self.rng.evaluate(i32(source['ApplyRate'])):result.add(source['Id'])
        self.events.append(dict(event='evaluate_weapon_debuffs',source_ids=sorted(result)))
        return result

    def terrain_list(self,root,unit,coord):
        if root is not self.map:raise ValueError('Wrong map repository')
        return self._terrain_rows(unit,coord)

    def calculate_hit_rate(self,actor,weapon_id,target,terrain,counter_type):
        weapon=self.get_weapon(actor,weapon_id)
        given=actor['scalar_corrections'];taken=target['scalar_corrections']
        attack=character_attack_value(actor['pilot'],weapon.base['attack_attributes'])
        target_mobility=reduced_target_stat(target,'mobility',given.get('AttackTargetMobilityReductionRate',0))
        result=compose_hit_rate(weapon.hit_rate(),target_action_inability=self.is_action_inability(target),
            absolute_evasion=i32(taken.get('AbsoluteEvasionCount',0))>0,
            attacker_sure_hit=i32(given.get('SureHitAttackCount',0))>0,
            mobility_correction=self.kernel.mobility_correction(actor['mobility'],attack,target_mobility,target['pilot']['Reaction']),
            ability_correction=i32(-taken.get('EvasionRate',0)),map_effect=terrain_hit_correction(terrain),counter_type=counter_type)
        self.events.append(dict(event='hit_rate',target=target['id'],value=result.value,counter_type=counter_type))
        return result

    def evaluate_hit(self,rate,broken,prediction):
        return evaluate_hit(rate.value,self.rng,sure_hit=rate.sure_hit,target_is_broken=broken,prediction=prediction)
    def evaluate_parry(self,unit,prediction):
        return evaluate_parry(unit['scalar_corrections'].get('ParryRate',0),self.rng,prediction=prediction)
    def evaluate_critical(self,actor,weapon,participation,prediction):
        rate=weapon.critical_rate()
        kind=evaluate_critical(rate,current_tension(actor),self.rng,
            guaranteed=i32(actor['scalar_corrections'].get('GuaranteedCriticalCount',0))>0,prediction=prediction)
        return dict(value=rate,kind=kind)

    def weapon_power(self,unit,weapon,distance):
        extreme=max(normal_max_range(w['range_min'],w['range_max'],sum_field(weapon_records(unit),'MaxRangeValue',w))
                    for w in unit['weapons'] if w['id'] not in unit.get('disabled_weapon_ids',()))
        resources=dict(unit,max_mp=12)  # Normal GetWeaponPower explicitly passes 12.
        return effective_power(resources,weapon.base,weapon.corrections,distance=distance,extreme_max=extreme)

    def has_damage_cut(self,unit):
        correction=unit.get('damage_cut_correction')
        if correction is None:
            if unit.get('damage_cut'):raise NotImplementedError('Import the limited DamageCut correction, not a boolean flag')
            return False
        # This binding rejects prediction at entry; only current remaining count applies.
        return i32(correction['remaining_count'])>0

    def create_damage_container(self,actor,weapon,target,counter_type,terrain,config):
        if config is not self.kernel.config:raise ValueError('Wrong damage config')
        return BoundDamageContainer(actor,weapon,target,i32(counter_type),terrain)

    def total_damage(self,container,power,critical_type,count):
        from .damage_caps import damage_cap
        from .hp_barrier import split_damage
        actor=container.attacker;target=container.target;weapon=container.weapon
        given=actor['scalar_corrections'];taken=target['scalar_corrections'];target_shields=shields(target)
        attack=character_attack_value(actor['pilot'],weapon.base['attack_attributes'])
        defense=reduced_target_stat(target,'defense',given.get('AttackTargetDefenseReductionRate',0))
        battle=self.kernel.battle_damage(power,actor['attack'],defense,attack,target['pilot']['Defense'],
            weapon_efficiency=damage_efficiency(weapon.capability,container.terrain))
        tension=current_tension(actor);critical=critical_type!=0
        final=dict(given_rate=given.get('DamageGivenCorrectionRate',0),given_flat=given.get('DamageGivenCorrectionValue',0),
            weapon_rate=sum_field(weapon_records(actor),'DamageCorrectionRate',weapon.base),
            critical_rate=given.get('CriticalDamageGivenCorrectionRate',0) if critical else 0,
            critical_flat=given.get('CriticalDamageGivenCorrectionValue',0) if critical else 0,
            tension_rate=self.coefficients.get({3:30,4:31,5:32}.get(tension),0),
            critical_bonus=self.coefficients[{2:33,3:34,4:35,5:36}[tension]] if critical else 0,
            taken_rate=sum_shields(target_shields,'ReductionRate'),taken_flat=sum_shields(target_shields,'ReductionValue'),
            defense_multiplier=self.kernel.defense_multiplier(container.counter_type,sum_shields(target_shields,'DefensiveReductionRate')),
            hit_count=count,damage_cut=self.has_damage_cut(target),negate_threshold=taken.get('DamageNegationValue',0),
            upper_limit=damage_cap(target.get('damage_taken_upper_limit_values',[])))
        total=self.kernel.final_damage(battle,**final)
        result=split_damage(target,weapon.base,total)
        self.events.append(dict(event='damage',target=target['id'],terrain=container.terrain,attack=actor['attack'],
            defense=defense,character_attack=attack,character_defense=target['pilot']['Defense'],
            weapon_efficiency=damage_efficiency(weapon.capability,container.terrain),power=power,
            battle_damage=battle,final_inputs=final,hp_damage=result[0],barrier_damage=result[1]))
        return result

    def en_attack(self,weapon,maximum):return en_attack_value(weapon.base['weapon_traits'],maximum)
    def mp_attack(self,weapon):return mp_attack_value(weapon.base['weapon_traits'])
    def drain_hp(self,weapon,damage,maximum):return first_drain(weapon.base['weapon_traits'],10,damage,maximum)
    def drain_en(self,weapon,damage,maximum):return first_drain(weapon.base['weapon_traits'],11,damage,maximum)
    def en_consumption(self,weapon):return en_consumption(weapon.base['en'],weapon.corrections,weapon.base)
    def mp_consumption(self,unit,weapon):
        cost=mp_consumption(weapon.base['base_mp_consumption'],weapon.corrections,weapon.base)
        return unit_mp_consumption(cost,True if cost is None else mp_change_enabled(unit))
    def hp_consumption(self,weapon,maximum):
        return hp_consumption(maximum,hp_consumption_rate(weapon.base['base_hp_consumption_rate'],weapon.corrections,weapon.base))

    def correction_damage_rate(self,actor,weapon,target):
        if weapon is None:return 0
        rate=i32(actor['scalar_corrections'].get('DamageGivenCorrectionRate',0)+
                 sum_field(weapon_records(actor),'DamageCorrectionRate',weapon.base))
        rate=i32(rate+self.coefficients.get({3:30,4:31,5:32}.get(current_tension(actor)),0))
        if target is None:return rate
        efficiency=weapon.efficiency_percent(self.standby_terrain(target))
        return correction_damage_percentage(rate,0,0,sum_shields(shields(target),'ReductionRate'),efficiency)
