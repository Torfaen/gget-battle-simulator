"""Concrete Always execution for the pinned stage's initial and weapon traits.

This executes on explicit repositories with explicit coordinates and phases.
It is not the complete stage lifecycle or live battle repository attachment.
"""
from copy import deepcopy
from types import SimpleNamespace
from .original import i32
from .trait_activation import (MasterTraitConditionServices,TraitConditionChecker,TraitExecutionContext,
    FIXED_STAGE_TRAIT_KINDS,FIXED_STAGE_WEAPON_PASSIVE_KINDS,master_condition_rows)
from .trait_effects import (VERIFIED_STAT_KINDS,KNOWN_ON_DEMAND_KINDS,refresh_runtime_stats,
    _validate_stat_baseline,_can_remove,_on_remove,_remove_stat)
from .trait_resource_effects import RESOURCE_FIELDS,resource_correction,resource_on_execute,cannot_remove_resource
from .trait_count_effects import COUNT_FIELDS,count_correction,count_side_effect
from .trait_consumption import remove_from_unit
from .trait_initial_lifecycle import execute_always_on_battle
from .weapon_corrections import WEAPON_FIELDS,SHIELD_KINDS


def preflight_initial_trait(trait,group):
    if group.get('prediction'):raise NotImplementedError('Separate prediction repository required')
    if group['source_type'] not in (1,2,3,4,5,7,8) or trait.get('source_unit_id') is not None:
        raise NotImplementedError('Initial source activation policy required')
    granted=group['source_type']==5
    allowed=({9,11,14} if granted else
             FIXED_STAGE_WEAPON_PASSIVE_KINDS if group['source_type']==4 else FIXED_STAGE_TRAIT_KINDS)
    if trait['trait_type'] not in allowed:
        raise NotImplementedError('Initial concrete trait class not connected')
    if 'supporter_definition' in trait:
        if trait['supporter_definition']['is_using_max']:
            raise NotImplementedError('Max-value supporter correction group required')
    else:
        row=trait['master_trait']
        if any(row[key] for key in ('ValueSourceConditionSetId','TraitBoostConditionSetId',
                                    'TargetTraitDuplicationGroupId','RangeMin','RangeMax')):
            raise NotImplementedError('Original value source, boost or aura selection required')
        # This stage has no target-weapon definitions. Keep the general weapon
        # compiler separate until its original definition selection is wired.
        if row['TargetWeaponConditionId']:
            raise NotImplementedError('Initial target-weapon definition selection required')
    if trait['trait_type'] in KNOWN_ON_DEMAND_KINDS:
        if trait['trait_type']!=3:raise NotImplementedError('Initial on-demand concrete class required')
    elif trait['action_timing']!=(3 if granted else 0):
        raise NotImplementedError('Event-timed initial effect registration required')


def build_initial_effect(master,trait,group,unit):
    """Build one already-selected concrete correction, without mutation."""
    preflight_initial_trait(trait,group)
    kind=trait['trait_type'];value=i32(trait['trait_value']);identity=trait['runtime_trait_id']
    if kind in KNOWN_ON_DEMAND_KINDS:raise ValueError('On-demand traits do not enter Always execution')
    effect=dict(master_trait_id=trait['master_trait_id'],can_remove=_can_remove,remove=_on_remove)
    if kind in RESOURCE_FIELDS:
        _validate_stat_baseline(unit,RESOURCE_FIELDS[kind])
        effect.update(stat_correction=resource_correction(trait),can_remove=cannot_remove_resource,remove=_remove_stat)
    elif kind in COUNT_FIELDS:
        if kind==19:unit['chance_step']['correction']
        else:unit['support_budget']['attack_limit' if kind==51 else 'defense_limit']
        effect.update(stat_correction=count_correction(trait),remove=_remove_count)
    elif kind in VERIFIED_STAT_KINDS:
        _validate_stat_baseline(unit,VERIFIED_STAT_KINDS[kind])
        effect.update(stat_correction={VERIFIED_STAT_KINDS[kind]:value},remove=_remove_stat)
    elif kind in WEAPON_FIELDS:
        effect['weapon_correction']=dict(condition=None,**{WEAPON_FIELDS[kind]:value})
    elif kind in SHIELD_KINDS:
        rows=master_condition_rows(master,trait,True)
        has_weapon=any(row.get('WeaponAttributeTypes') for row in rows)
        has_attack=any(row.get('WeaponAttackAttributeTypes') for row in rows)
        penetration=kind!=16 and ((value>0 and has_weapon) or has_attack)
        effect['shield_correction']=dict(id=identity,IsPenetrationAffected=bool(penetration),**{SHIELD_KINDS[kind]:value})
    else:raise NotImplementedError('Initial concrete effect builder required')
    return effect


def _remove_count(trait,unit):
    count_side_effect(trait,unit,removing=True)
    refresh_runtime_stats(unit,excluding=trait['runtime_trait_id'])


def execute_initial_trait(master,trait,group,unit):
    """Common Execute identity gate, correction installation, first callback."""
    identity=trait['runtime_trait_id']
    if identity in unit.get('runtime_trait_effects',{}):return False
    effect=build_initial_effect(master,trait,group,unit)
    unit.setdefault('runtime_trait_effects',{})[identity]=effect
    if 'stat_correction' in effect:refresh_runtime_stats(unit)
    if trait['trait_type'] in RESOURCE_FIELDS:resource_on_execute(trait,unit)
    elif trait['trait_type'] in COUNT_FIELDS:count_side_effect(trait,unit)
    return True


class InitialAlwaysRuntime:
    """Execute verified Always dispatch over this fixed shared trait repository.

    Source-type8 recipient restriction and ordered activation come from the
    common dispatcher. No source in this checked subset is an aura or has a
    boost/value-source condition; those are rejected, not defaulted to success.
    """
    def __init__(self,master,groups,units,non_unit_owners):
        self.master=master;self.groups=groups;self.units=units;self.non_unit_owners=non_unit_owners
        for group in groups.values():
            for trait in group['values']:preflight_initial_trait(trait,group)
        # Validate the entire condition vocabulary before applying any effect.
        MasterTraitConditionServices(master,groups,units,non_unit_owners)

    def execute(self,context,participation=None,*,weapon=None,units=None):
        # A provider constructs a fresh context at the original dispatcher
        # boundary. Explicit context values remain useful for narrow audits.
        traces=[];effects=[]
        conditions=MasterTraitConditionServices(self.master,self.groups,self.units,self.non_unit_owners,
            weapon=weapon,trace=traces)
        checker=TraitConditionChecker(conditions)
        traits=[t for g in self.groups.values() for t in g['values'] if t['trait_type'] not in KNOWN_ON_DEMAND_KINDS]
        def execute(trait,unit,boost):
            first=execute_initial_trait(self.master,trait,conditions.parent(trait['runtime_trait_id']),unit)
            effects.append(dict(runtime_trait_id=trait['runtime_trait_id'],unit_id=unit['id'],first=first))
        services=SimpleNamespace(create_context=lambda battle:context(battle) if callable(context) else context,
            always_traits=lambda:traits,
            active=checker.active,parent=conditions.parent,target=checker.target,is_aura=lambda group:False,
            boost_count=lambda trait,owner:None,execute=execute,remove=remove_from_unit,return_contexts=lambda:None)
        result=execute_always_on_battle(services,self.units.values() if units is None else units,participation)
        result.update(effects=effects,condition_trace=traces)
        # All fixed definitions now have concrete handlers. Inactive traits
        # need no installed correction; HP-rate on-demand traits return a null
        # correction from their original CreateCorrectionInternal.
        for group in self.groups.values():group['effect_registration_complete']=True
        return result

    def on_demand(self,context,timing,units,participation=None,*,owner_ids=None,weapon=None,current_phase=None):
        from .trait_on_demand import select_on_demand_traits
        selected=select_on_demand_traits(self.groups,timing,owner_ids=owner_ids)
        return self.on_demand_selected(context,selected,timing,units,participation,
            weapon=weapon,current_phase=current_phase)

    def on_demand_selected(self,context,traits,timing,units,participation=None,*,weapon=None,current_phase=None):
        from .trait_on_demand import execute_on_demand,execute_hp_change_rate
        from .trait_turn_progress import standard_satisfies
        trace=[];conditions=MasterTraitConditionServices(self.master,self.groups,self.units,self.non_unit_owners,
            weapon=weapon,trace=trace)
        checker=TraitConditionChecker(conditions)
        def phase_policy(parent,ctx):
            if current_phase is None:raise ValueError('Explicit current phase required for TurnProgress')
            return standard_satisfies(parent,current_phase)
        services=SimpleNamespace(create_context=lambda battle:context(battle) if callable(context) else context,
            parent=conditions.parent,
            turn_progress=phase_policy,active=checker.active,target=checker.target,
            execute=execute_hp_change_rate,return_contexts=lambda:None)
        selected=list(traits)
        results=execute_on_demand(services,selected,timing,units,participation)
        return dict(results=results,condition_trace=trace,selected_runtime_trait_ids=[t['runtime_trait_id'] for t in selected])

    def consume(self,current_phase,timing,*,owner_ids=None,trait_ids=None):
        from .trait_consumption import consume_trait_count
        return consume_trait_count(self.groups,self.units.values(),current_phase,timing,
            owner_ids=owner_ids,trait_ids=trait_ids)

    def _initial_services(self,context,current_phase):
        from .weapon_use import reflect_mp_change
        return SimpleNamespace(always=lambda units,b:self.execute(context,b,units=units),
            on_demand=lambda timing,units,b:self.on_demand(context,timing,units,b,current_phase=current_phase),
            consume=lambda timing,owners,ids:self.consume(current_phase,timing,owner_ids=owners,trait_ids=ids),
            reflect_mp=reflect_mp_change)

    def on_stage_start(self,context,current_phase):
        from .trait_initial_lifecycle import execute_on_stage_start
        return execute_on_stage_start(self._initial_services(context,current_phase),self.units.values())

    def on_first_turn_start(self,context,current_phase):
        from .trait_initial_lifecycle import execute_on_first_turn_start
        return execute_on_first_turn_start(self._initial_services(context,current_phase),self.units.values())

    def on_own_phase_start(self,context,current_phase,units):
        from .trait_initial_lifecycle import execute_on_own_phase_start
        return execute_on_own_phase_start(self._initial_services(context,current_phase),units)

    def consume_own_phase_results(self,current_phase,results):
        from .trait_initial_lifecycle import consume_own_phase_results
        services=SimpleNamespace(consume=lambda timing,owners,ids:
            self.consume(current_phase,timing,owner_ids=owners,trait_ids=ids))
        return consume_own_phase_results(services,results)

    def immediate_hit_event(self,context,current_phase,unit,timing,participation,*,weapon):
        from .trait_initial_lifecycle import execute_immediate_hit_event
        if self.units.get(unit['id']) is not unit:raise ValueError('Canonical shared unit required')
        services=SimpleNamespace(
            on_demand=lambda timing,units,b,owner_ids:self.on_demand(context,timing,units,b,
                owner_ids=owner_ids,weapon=weapon,current_phase=current_phase),
            consume=lambda timing,owners,ids:self.consume(current_phase,timing,owner_ids=owners,trait_ids=ids),
            always=lambda units,b:self.execute(context,b,weapon=weapon,units=units))
        return execute_immediate_hit_event(services,unit,timing,participation)

    def _battle_end_services(self,context,current_phase,weapon):
        from .battle_user_records import unit_existence
        from .trait_consumption import oldest_consumable_trait_id
        return SimpleNamespace(existence=unit_existence,
            on_demand=lambda timing,units,b,owner_ids:self.on_demand(context,timing,units,b,
                owner_ids=owner_ids,weapon=weapon,current_phase=current_phase),
            consume=lambda timing,owners,ids:self.consume(current_phase,timing,owner_ids=owners,trait_ids=ids),
            oldest=lambda owner,timing:oldest_consumable_trait_id(self.groups,owner,timing))

    def battle_end_hit_event(self,context,current_phase,unit,timing,participation,*,weapon=None):
        from .trait_battle_end import execute_after_hit_event
        if self.units.get(unit['id']) is not unit:raise ValueError('Canonical shared unit required')
        return execute_after_hit_event(self._battle_end_services(context,current_phase,weapon),unit,timing,participation)

    def on_battle_end(self,context,current_phase,units,participation,*,weapon=None):
        from .trait_battle_end import execute_on_battle_end
        units=tuple(units)
        if any(self.units.get(unit['id']) is not unit for unit in units):raise ValueError('Canonical shared units required')
        return execute_on_battle_end(self._battle_end_services(context,current_phase,weapon),units,participation)

    def consume_battle_end_action(self,current_phase,unit,timing):
        from .trait_battle_end import consume_battle_end_action
        if self.units.get(unit['id']) is not unit:raise ValueError('Canonical shared unit required')
        return consume_battle_end_action(self._battle_end_services(None,current_phase,None),unit['id'],timing)

    def on_regeneration(self,context,current_phase,unit,participation,*,weapon=None):
        from .trait_battle_end import execute_on_regeneration
        if self.units.get(unit['id']) is not unit:raise ValueError('Canonical shared unit required')
        return execute_on_regeneration(self._battle_end_services(context,current_phase,weapon),unit,participation)

    def on_unit_defeated(self,context,current_phase,units):
        from .trait_unit_defeated import execute_on_unit_defeated,MasterDefeatedConditions
        from .trait_on_demand import select_on_demand_traits
        units=tuple(units)
        if any(self.units.get(u['id']) is not u for u in units):raise ValueError('Canonical defeated-event participants required')
        services=SimpleNamespace(traits=lambda timing:select_on_demand_traits(self.groups,timing),
            owner=self.units.get,count=MasterDefeatedConditions(self.master).count,
            on_demand=lambda traits,timing,targets,p:self.on_demand_selected(context,traits,timing,targets,p,
                current_phase=current_phase),
            consume=lambda timing,owners,ids:self.consume(current_phase,timing,owner_ids=owners,trait_ids=ids))
        return execute_on_unit_defeated(services,units)

    def on_tension_changed(self,context,current_phase,units,participation,*,weapon=None):
        from .trait_tension_changed import execute_on_tension_changed
        units=tuple(units)
        if any(self.units.get(u['id']) is not u for u in units):raise ValueError('Canonical tension-event participants required')
        services=self._battle_end_services(context,current_phase,weapon)
        services.always=lambda targets,p:self.execute(context,p,weapon=weapon,units=targets)
        return execute_on_tension_changed(services,units,participation)


def isolated_initial_state(env):
    """Fresh units from recorded pre-passive baselines, for integration auditing.

    Keeps imported placement and equipment. No mutations to the current env;
    this explicitly excludes complete original entity/context construction.
    """
    from dataclasses import asdict
    from .mechanics import SupportBudget
    inventory=env.initial_trait_inventory;units={}
    for identity in inventory['unit_identities']:
        unit=deepcopy(env.units[identity]);unit.update(deepcopy(unit['_passive_initial_state']))
        unit.update(scalar_corrections={},_runtime_scalar_base_corrections={},_runtime_stat_fields=[],
            runtime_trait_effects={},combat_traits=[],applied_passive_ids=[],support_budget=asdict(SupportBudget()))
        unit['chance_step'].update(correction=0,once=0,guaranteed_once=0)
        units[identity]=unit
    groups=deepcopy(inventory['groups']);owners=deepcopy(inventory['non_unit_owners'])
    return InitialAlwaysRuntime(env.rules.master,groups,units,owners)
