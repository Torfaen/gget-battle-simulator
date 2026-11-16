"""Original ordinary AttackService application, separately from scheduling.

The concrete binding applies the fixed roster's ordinary entity mutations.
Damage-barrier/cut source removal requires a separate repository service; the
fixed binding rejects such corrections before mutating either participant.
"""
from copy import deepcopy
from .original import i32
from .unit_damage import damage_normal,evade_attack,add_mp_change,is_broken
from .regeneration import recover_hp_by_value
from .weapon_resource_damage import plus_en
from .weapon_use import use_weapon_with_getters
from .weapon_corrections import mp_consumption


def attack_result(services,target,result,*,prediction=False):
    # IsHit is read even when IsBroken already selected the damage branch.
    broken=services.is_broken(target)
    hit=result.is_hit
    if broken or hit:
        escape=services.can_escape(target)
        services.damage(target,result.hit_hp_damage,result.hit_hp_barrier_damage,escape)
        if not prediction:
            services.remove_damage_barrier(target)
            services.remove_damage_cut(target)
        services.recover_en(target,i32(-(result.hit_en_damage or 0)))
        services.add_mp_change(target,i32(-(result.hit_mp_damage or 0)))
    else:services.evade_attack(target)


def attack_by_weapon(services,attacker,target,result,*,prediction=False):
    # UseWeapon reads the bound weapon's costs; DTO fee fields are display data.
    services.use_weapon(attacker,result.raw['attack_weapon'])
    services.recover_hp(attacker,result.drained_hp or 0)
    services.recover_en(attacker,result.drained_en or 0)
    attack_result(services,target,result,prediction=prediction)


class FixedStageAttackApplication:
    def __init__(self,calculation):
        self.calculation=calculation;self.runtime=calculation.runtime;self.events=[]

    def apply(self,attacker,target,result,*,prediction=False):
        self.preflight(attacker,target,result,prediction=prediction)
        self.events=[];before={u['id']:deepcopy(u) for u in (attacker,target)}
        attack_by_weapon(self,attacker,target,result)
        return dict(events=deepcopy(self.events),before=before,
                    after={u['id']:deepcopy(u) for u in (attacker,target)})

    def preflight(self,attacker,target,result,*,prediction=False):
        if prediction:raise NotImplementedError('Independent prediction state and damage accumulators required')
        from .battle_calculation_runtime import BoundUnitWeapon
        weapon=result.raw['attack_weapon']
        if not isinstance(weapon,BoundUnitWeapon):raise TypeError('Concrete bound ordinary weapon required')
        if not any(weapon.base is w for w in attacker['weapons']):raise ValueError('Weapon does not belong to attacker')
        for unit in (attacker,target):
            if self.runtime.units.get(unit['id']) is not unit:raise ValueError('Canonical shared repository participants required')
            if unit.get('prediction'):raise NotImplementedError('Actual entities required')
        # These removals can clean other groups/owners; a local barrier-list pop
        # would bypass source identity and repository cleanup.
        if target.get('hp_barriers') or target.get('hp_barrier',0) or target.get('damage_cut_correction') is not None or target.get('damage_cut'):
            raise NotImplementedError('Bind original barrier/cut source removal before applying this target')
        self.calculation.can_escape(target)  # Preflight unported escape mechanisms.

    def is_broken(self,unit):return is_broken(unit)
    def can_escape(self,unit):return self.calculation.can_escape(unit)
    def use_weapon(self,unit,weapon):
        costs=use_weapon_with_getters(unit,
            lambda:self.calculation.en_consumption(weapon),
            lambda:mp_consumption(weapon.base['base_mp_consumption'],weapon.corrections,weapon.base),
            lambda maximum:self.calculation.hp_consumption(weapon,maximum))
        self.events.append(dict(event='use_weapon',unit=unit['id'],weapon=weapon.base['id'],**costs))
    def recover_hp(self,unit,value):
        recover_hp_by_value(unit,value)
        self.events.append(dict(event='recover_hp',unit=unit['id'],value=value))
    def recover_en(self,unit,value):
        plus_en(unit,value)
        self.events.append(dict(event='recover_en',unit=unit['id'],value=value))
    def damage(self,unit,hp,barrier,escape):
        damage_normal(unit,hp,barrier,escape)
        self.events.append(dict(event='damage',unit=unit['id'],hp=hp,barrier=barrier,can_escape=escape))
    def remove_damage_barrier(self,unit):
        self.events.append(dict(event='remove_damage_barrier',unit=unit['id'],no_correction=True))
    def remove_damage_cut(self,unit):
        self.events.append(dict(event='remove_damage_cut',unit=unit['id'],no_correction=True))
    def add_mp_change(self,unit,value):
        add_mp_change(unit,value)
        self.events.append(dict(event='add_mp_change',unit=unit['id'],value=value))
    def evade_attack(self,unit):
        evade_attack(unit)
        self.events.append(dict(event='evade_attack',unit=unit['id']))
