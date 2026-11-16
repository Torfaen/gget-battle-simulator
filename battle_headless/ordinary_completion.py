"""Shared ordinary completion services after regeneration.

An explicit engagement service can write support statistics and combat logs
between weapon disable and escape. Earlier score, final chance/movement and
defeated map removal remain separate caller boundaries.
The caller must retain the original earlier/later boundaries. Escape handling
is exact only for current forms with no escape mechanism (the pinned roster).
"""
from copy import copy,deepcopy
from .original import i32
from .battle_resources import mp_change_info,reflect_en_change
from .weapon_use import reflect_mp_change
from .tension import current_tension
from .battle_user_records import entity_is_alive,unit_existence,unit_side
from .unit_mechanisms import can_escape_without_mechanism


def execute_resource_reflection(services,units,participation):
    """Original cached-participant loop, with supplied resource/property services.

    Every participant reflects both resources, even when dead. Tension changes
    and high-tension records require IsAlive after reflection. An async caller
    may yield before participants 3, 5, ...; no gameplay mutation occurs there.
    """
    changed=[];rows=[]
    for unit in units:
        before=services.tension(unit);info=services.mp_info(unit)
        services.reflect_en(unit);services.reflect_mp(unit)
        after=None
        if services.alive(unit):
            after=services.tension(unit)
            if before!=after:
                if after in (4,5):services.record(2 if after==4 else 3,unit)
                changed.append(unit)
        rows.append(dict(unit_id=unit['id'],mp_change=info,tension_before=before,tension_after=after))
    tension=services.tension_changed(changed,participation)
    return dict(units=rows,changed_unit_ids=[u['id'] for u in changed],tension_event=tension)


def execute_no_escape(services,units):
    """IngameEscapeService.Execute no-mechanism branch, including empty Always."""
    checked=[]
    for unit in units:
        if i32(unit['hp'])>0:continue
        services.require_no_escape(unit);checked.append(unit['id'])
    always=services.always([],None)
    return dict(checked_unit_ids=checked,changed_unit_ids=[],escaped_units={},always=always)


class FixedStageBattleCompletion:
    """Whole-stage preflight preserves shared identities and record state.

    Preflight rejects unsupported form changes before any original-state write;
    this intentionally does not reproduce exception-path partial mutations.
    """
    def __init__(self,calculation,records):
        self.calculation=calculation;self.runtime=calculation.runtime;self.records=records;self.events=[]

    def execute(self,participation,*,engagement=None,log_results=None,selected_weapon_id=None,cached_support_defender=None):
        p=participation
        if engagement is None and log_results is not None:raise ValueError('Explicit engagement service required')
        if engagement is not None and (engagement.calculation is not self.calculation or engagement.records is not self.records):
            raise ValueError('Shared completion/log calculations and user records required')
        if engagement is not None and log_results is None:raise ValueError('Original outer log results required')
        units=[*p.participants,p.main_attacker_unit,p.main_target_unit,p.support_defender_unit,
               p.attack_side_support_defender_unit,*p.support_attack_units,*p.counter_support_units]
        if any(u is not None and self.runtime.units.get(u['id']) is not u for u in units):
            raise ValueError('Canonical completion participants required')
        trial_units,trial_groups,trial_p,trial_records,trial_results,trial_cached,trial_logs=deepcopy((
            self.runtime.units,self.runtime.groups,p,self.records,log_results,cached_support_defender,
            None if engagement is None else engagement.logs))
        runtime=copy(self.runtime);runtime.units=trial_units;runtime.groups=trial_groups
        calculation=copy(self.calculation);calculation.runtime=runtime;calculation.events=[]
        calculation.passives=copy(calculation.passives);calculation.passives.runtime=runtime
        trial_engagement=None
        if engagement is not None:
            trial_engagement=copy(engagement);trial_engagement.calculation=calculation
            trial_engagement.records=trial_records;trial_engagement.logs=trial_logs;trial_engagement.events=[]
        FixedStageBattleCompletion(calculation,trial_records)._execute(trial_p,trial_engagement,trial_results,selected_weapon_id,trial_cached)
        return self._execute(p,engagement,log_results,selected_weapon_id,cached_support_defender)

    def _execute(self,p,engagement=None,log_results=None,selected_weapon_id=None,cached_support_defender=None):
        self.events=[];self.participation=p
        self.weapon=None if p.active_attacker_weapon is None else p.active_attacker_weapon.base
        units=tuple({u['id']:u for u in p.participants}.values())
        self.calculation.disable_weapon_traits(units)
        self.events.append(dict(event='disable_weapon_traits',unit_ids=[u['id'] for u in units]))
        if engagement is not None:
            result=engagement.execute(p,log_results,selected_weapon_id=selected_weapon_id,cached_support_defender=cached_support_defender)
            self.events.append(dict(event='engagement_logs',result=result))
        escape=execute_no_escape(self,units)
        self.events.append(dict(event='escape',result=escape))
        defeated=self.runtime.on_unit_defeated(self.calculation.context,self.calculation.phase,units)
        self.events.append(dict(event='unit_defeated',result=defeated))
        reflection=execute_resource_reflection(self,units,p)
        return dict(escape=escape,defeated=defeated,reflection=reflection,events=self.events,
                    user_records=self.records.snapshot())

    def require_no_escape(self,unit):can_escape_without_mechanism(self.runtime.master,unit)
    def always(self,units,p):return self.runtime.execute(self.calculation.context,p,units=units,weapon=self.weapon)
    def tension(self,unit):return current_tension(unit)
    def mp_info(self,unit):return mp_change_info(unit)
    def reflect_en(self,unit):reflect_en_change(unit)
    def reflect_mp(self,unit):reflect_mp_change(unit)
    def alive(self,unit):return entity_is_alive(unit_existence(unit))
    def record(self,kind,unit):
        changed=self.records.increment(kind,unit_side(unit))
        self.events.append(dict(event='tension_record',kind=kind,unit_id=unit['id'],changed=changed))
    def tension_changed(self,units,p):
        result=self.runtime.on_tension_changed(self.calculation.context,self.calculation.phase,units,p,weapon=self.weapon)
        self.events.append(dict(event='tension_changed',unit_ids=[u['id'] for u in units],result=result))
        return result
