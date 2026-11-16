"""Original weapon passive selection, persistent sets and temporary corrections.

These entry points must be called at the calculator's original boundaries.
They do not choose strike order or substitute a prediction repository.
"""
from copy import deepcopy
from types import SimpleNamespace
from .original import i32
from .trait_consumption import remove_from_unit


def evaluate_weapon_passives(services,owner_id,weapon_traits,participation,*,prediction=False):
    """_EvaluateWeaponPassiveTraitApplyRate, including prediction's rate gate."""
    applied=set()
    context=services.create_context(participation)
    for source in weapon_traits:
        if source is None or source['WeaponTraitTypeIndex']!=3:continue
        rate=i32(source['ApplyRate'])
        if prediction and rate<100:continue
        if not services.condition(owner_id,source,participation,context):continue
        if services.evaluate_rate(rate):applied.add(source['Id'])
    return applied


def enable_weapon_passives(services,owner_id,weapon_traits,applied_ids):
    """Enable existing source identity; only a missing set is created."""
    if not applied_ids:return
    for source in weapon_traits:
        if source is None or source['WeaponTraitTypeIndex']!=3 or source['Id'] not in applied_ids:continue
        if services.enable_trait(owner_id,4,source['Id']):continue
        master_set=services.master_set(source)
        services.create_and_save(owner_id=owner_id,source_type=4,source_id=source['Id'],
            source_name=services.source_name(source),master_set=master_set,
            source_unit_id=owner_id if master_set['IsEnabledWithGrantOwnerActive'] else None,
            stack_count=1,additional_limit=0,additional_trait_value=0)


def evaluate_and_enable_weapon_passives(services,owner_id,weapon_traits,participation,*,prediction=False):
    applied=evaluate_weapon_passives(services,owner_id,weapon_traits,participation,prediction=prediction)
    enable_weapon_passives(services,owner_id,weapon_traits,applied)
    return applied


def disable_weapon_traits(services,units):
    """DisableWeaponTraits visits every source4 set, not just these units' owners."""
    units=tuple(units)
    for group in services.find_by_source(4):
        for trait in group['values']:
            for unit in units:services.remove(trait,unit)
        services.set_enabled(group,False)


class FixedStageWeaponPassiveRuntime:
    """Connect the fixed stage's weapon sets to the same runtime as initial sets.

    factory must continue the recorded initial identity counters. Name lookup,
    current phase, context creation and RNG are explicit caller inputs.
    """
    def __init__(self,runtime,factory,*,source_name):
        self.runtime=runtime;self.factory=factory;self.source_name=source_name
        self.details={row['TraitSetId']:row for row in runtime.master.tables['m_trait_set_detail']}

    def _enable(self,owner_id,source_type,source_id):
        for group in self.runtime.groups.values():
            if (group['owner_unit_id'],group['source_type'],group['source_id'])==(owner_id,source_type,source_id):
                group['enabled_internal']=True
                return True
        return False

    def _create(self,current_phase,**request):
        from .trait_initial_effects import preflight_initial_trait
        from .trait_activation import MasterTraitConditionServices
        master_set=request.pop('master_set')
        if not any(row['Id']==master_set['TraitSetId'] for row in self.runtime.master.tables['m_trait_set']):return None
        factory=deepcopy(self.factory)
        group=factory.create_set(self.runtime.master,master_set['TraitSetId'],request.pop('owner_id'),current_phase,**request)
        for trait in group['values']:preflight_initial_trait(trait,group)
        groups=dict(self.runtime.groups)
        if group['runtime_set_id'] in groups:raise ValueError('Weapon factory must continue the initial identity sequence')
        groups[group['runtime_set_id']]=group
        MasterTraitConditionServices(self.runtime.master,groups,self.runtime.units,self.runtime.non_unit_owners)
        self.runtime.groups[group['runtime_set_id']]=group
        self.factory.__dict__.update(factory.__dict__)
        return group

    def enable(self,owner_id,weapon,participation,context_factory,rng,current_phase):
        from .trait_activation import weapon_trait_condition_matches
        if not callable(context_factory):raise TypeError('Weapon activation requires a fresh context factory')
        services=SimpleNamespace(create_context=context_factory,
            condition=lambda owner,source,battle,context:weapon_trait_condition_matches(
                self.runtime.master,self.runtime.units,self.runtime.non_unit_owners,owner,source,context,battle,weapon),
            evaluate_rate=rng.evaluate,enable_trait=self._enable,
            master_set=lambda source:self.details[source['TargetValue']],source_name=self.source_name,
            create_and_save=lambda **request:self._create(current_phase,**request))
        return evaluate_and_enable_weapon_passives(services,owner_id,weapon['weapon_traits'],participation)

    def disable(self,units):
        services=SimpleNamespace(find_by_source=lambda source:[g for g in self.runtime.groups.values() if g['source_type']==source],
            remove=remove_from_unit,set_enabled=lambda group,value:group.__setitem__('enabled_internal',value))
        disable_weapon_traits(services,units)
