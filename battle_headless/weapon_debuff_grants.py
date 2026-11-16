"""Original weapon-debuff grant orchestration on the shared trait repository.

Grant creates states and callbacks; the subsequent Always dispatch installs
corrections. Hit/parry gates and immediate battle events belong to AttackUseCase.
"""
from copy import deepcopy
from .original import i32
from .trait_grants import adjust_stack_count,can_overwrite_set
from .trait_consumption import is_set_enabled,remove_set_from_unit
from .weapon_debuffs import addition_for_group


def find_duplicate(services,owner_id,duplication_group):
    """_FindDuplicationTrait 0x5f94c7c, repository order and short-circuit gates."""
    if not duplication_group:return None
    for group in services.owner_groups(owner_id):
        if not services.enabled(group):continue
        if not services.is_buff(group):continue
        if services.is_aura(group):continue
        if group['master_detail']['DuplicationGroupId']==duplication_group:return group
    return None


def grant_trait_set(services,detail,active_count,target,source_unit_id,value,duration):
    """_GrantTraitSetInternal 0x5f947f4; bool is acceptance, not creation."""
    stack=services.adjust_stack(detail,active_count)
    old=services.find_duplicate(services.unit_id(target),detail['DuplicationGroupId'])
    if old is not None:
        if not services.can_overwrite(old,detail,stack,value,duration):return False,None
        for unit in services.all_units():services.remove_effects(old,unit)
        services.remove_group(old)
    services.create_and_save(services.unit_id(target),5,detail['TraitSetId'],
        detail,source_unit_id,stack,value,duration)
    return True,old


def grant_weapon_debuff_traits(services,actor,target,traits,selected_ids):
    """GrantWeaponDebuffTrait 0x5f95b7c, including returned display-list folding."""
    if not selected_ids:return None,None
    granted=[];replaced=None
    correction=services.correction(actor)
    for source in traits:
        if source is None or source['WeaponTraitTypeIndex']!=12 or source['Id'] not in selected_ids:continue
        services.use_weapon_trait(actor,source['Id'])
        detail=services.master_set(source)
        addition=services.debuff_addition(correction,detail['DuplicationGroupId'])
        count=services.active_count(actor,source['Id'])
        source_unit_id=services.unit_id(actor) if detail['IsEnabledWithGrantOwnerActive'] else None
        accepted,old=services.grant(detail,count,target,source_unit_id,
            addition['value'] if addition else 0,addition['duration'] if addition else 0)
        if old is not None:
            duplicate=old['master_detail']['DuplicationGroupId']
            if any(services.master_set(item)['DuplicationGroupId']==duplicate for item in granted):
                granted[:]=[item for item in granted if services.master_set(item)['DuplicationGroupId']!=duplicate]
            else:replaced=old
        if accepted:granted.append(source)
    return granted,replaced


class FixedStageWeaponDebuffGrants:
    """Actual fixed-stage grants, preserving factory sequence and deferred effects.

    The complete request is preflighted on isolated state. This deliberately
    excludes native exception partial writes and grant-owner propagation.
    """
    def __init__(self,runtime,factory,phase,*,source_name):
        self.runtime=runtime;self.factory=factory;self.phase=phase;self.source_name=source_name
        self.details={d['TraitSetId']:d for d in runtime.master.tables['m_trait_set_detail']}
        self.definitions={}
        for row in runtime.master.tables['m_trait_set']:
            self.definitions.setdefault(row['Id'],[]).append(runtime.master.index['m_trait'][row['TraitId']])

    def grant_selected(self,actor,target,traits,selected_ids):
        from types import SimpleNamespace
        for unit in (actor,target):
            if self.runtime.units.get(unit['id']) is not unit:raise ValueError('Canonical shared unit required')
        selected_ids=set(selected_ids or ())
        trial=SimpleNamespace(master=self.runtime.master,groups=deepcopy(self.runtime.groups),
            units=deepcopy(self.runtime.units),non_unit_owners=self.runtime.non_unit_owners)
        preview=FixedStageWeaponDebuffGrants(trial,deepcopy(self.factory),self.phase,source_name=self.source_name)
        grant_weapon_debuff_traits(preview,trial.units[actor['id']],trial.units[target['id']],traits,selected_ids)
        granted,replaced=grant_weapon_debuff_traits(self,actor,target,traits,selected_ids)
        return dict(granted_weapon_traits=granted,replaced_set=replaced)

    def grant_from_result(self,actor,damage_target,result):
        """The common AttackUseCase IsHit gate; no new rate roll here."""
        if not result.is_hit:return dict(granted_weapon_traits=None,replaced_set=None)
        from .battle_calculation_runtime import BoundUnitWeapon
        weapon=result.raw['attack_weapon']
        if not isinstance(weapon,BoundUnitWeapon) or not any(w is weapon.base for w in actor['weapons']):
            raise ValueError('Original bound actor weapon required')
        return self.grant_selected(actor,damage_target,weapon.base['weapon_traits'],result.raw['debuff_weapon_trait_ids'])

    def unit_id(self,unit):return unit['id']
    def correction(self,unit):
        if unit.get('weapon_debuff_additions'):
            raise NotImplementedError('Legacy debuff additions cannot enter the shared correction repository')
        # No fixed-roster source constructs types 88/89. Reject newly supplied
        # additions until their original shared correction builders are wired.
        if any(t['owner_unit_id']==unit['id'] and t['trait_type'] in (88,89)
               for g in self.runtime.groups.values() for t in g['values']):
            raise NotImplementedError('Shared weapon-debuff addition registration required')
        return ()
    def use_weapon_trait(self,unit,identity):
        counts=unit.setdefault('weapon_trait_active_counts',{});key=str(identity)
        counts[key]=i32(counts.get(key,0)+1)
    def active_count(self,unit,identity):return unit.get('weapon_trait_active_counts',{}).get(str(identity),0)
    def master_set(self,source):return self.details[source['TargetValue']]
    def debuff_addition(self,correction,group):return addition_for_group(correction,group)
    def grant(self,*args):return grant_trait_set(self,*args)
    def adjust_stack(self,detail,count):return adjust_stack_count(detail,count)
    def owner_groups(self,identity):return (g for g in self.runtime.groups.values() if g['owner_unit_id']==identity)
    def enabled(self,group):return is_set_enabled(group)
    def is_buff(self,group):
        self._check_no_aura(group)
        return any(t['source_type'] in (5,6,9,14,15,19) for t in group['values'])
    def is_aura(self,group):
        self._check_no_aura(group)
        return any(t['source_type']==14 for t in group['values'])
    def _check_no_aura(self,group):
        from .trait_activation import master_condition_rows,validate_condition_rows
        for trait in group['values']:
            # Range condition vocabulary is unsupported by this fixed runtime.
            for active in (False,True):validate_condition_rows(master_condition_rows(self.runtime.master,trait,active))
            if any(trait.get('master_trait',{}).get(key) for key in ('RangeMin','RangeMax')):
                raise NotImplementedError('Aura condition classification required')
    def find_duplicate(self,owner,group):return find_duplicate(self,owner,group)
    def can_overwrite(self,old,detail,stack,value,duration):
        return can_overwrite_set(old,self.definitions.get(detail['TraitSetId'],[]),stack,
            additional_trait_value=value,additional_limit=duration)
    def all_units(self):return self.runtime.units.values()
    def remove_effects(self,group,unit):return remove_set_from_unit(group,unit)
    def remove_group(self,group):del self.runtime.groups[group['runtime_set_id']]
    def create_and_save(self,owner,source_type,source_id,detail,source_unit,stack,value,duration):
        from .trait_initial_effects import preflight_initial_trait
        from .trait_activation import MasterTraitConditionServices
        if not self.definitions.get(detail['TraitSetId']):return None
        group=self.factory.create_set(self.runtime.master,detail['TraitSetId'],owner,self.phase,
            source_type=source_type,source_id=source_id,source_name=self.source_name(detail),
            source_unit_id=source_unit,stack_count=stack,additional_trait_value=value,additional_limit=duration)
        for trait in group['values']:preflight_initial_trait(trait,group)
        groups=self.runtime.groups
        ids={t['runtime_trait_id'] for g in groups.values() for t in g['values']}
        if group['runtime_set_id'] in groups or any(t['runtime_trait_id'] in ids for t in group['values']):
            raise ValueError('Debuff factory must continue the shared runtime identity sequence')
        MasterTraitConditionServices(self.runtime.master,{**groups,group['runtime_set_id']:group},
            self.runtime.units,self.runtime.non_unit_owners)
        # All concrete handlers are known. No effect is installed by creation.
        group['effect_registration_complete']=True
        groups[group['runtime_set_id']]=group
        return group
