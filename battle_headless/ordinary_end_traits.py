"""AttackUseCase's post-movement, pre-regeneration end-trait phase.

The enclosing caller must finish MP allocation, support-defense/movement
consumption and score/log boundaries first. Async presentation is omitted;
regeneration, defeat cleanup and final MP reflection follow this phase.
"""
from copy import copy,deepcopy


def execute_end_traits(services,state,participation):
    for field,timing in (('player1_weapon_hit',13),('player2_weapon_hit',13),
                         ('player2_damaged',10),('player1_damaged',10)):
        for identity in getattr(state,field):services.event(identity,timing,participation)
    if state.player1_evaded:services.evasion(participation.main_attacker)
    if state.player2_evaded:services.evasion(participation.main_target)
    # Participants is the original cached set, not recomputed role selection.
    units=list({unit['id']:unit for unit in participation.participants}.values())
    services.battle_end(units,participation)
    services.always(units,participation)


class FixedStageEndTraits:
    """Shared explicit end-trait phase with a complete mutation preflight.

Uses the supplied accumulated strike sets and participation, including its
active weapon. This does not decide whether strikes/guards were eligible.
"""
    def __init__(self,calculation):self.calculation=calculation;self.runtime=calculation.runtime;self.events=[]

    def execute(self,state,participation):
        p=participation
        units=[*p.participants,p.main_attacker_unit,p.main_target_unit,p.support_defender_unit,p.attack_side_support_defender_unit,
               *p.support_attack_units,*p.counter_support_units]
        for unit in units:
            if unit is not None and self.runtime.units.get(unit['id']) is not unit:raise ValueError('Canonical end-trait participants required')
        for field in ('player1_weapon_hit','player2_weapon_hit','player1_damaged','player2_damaged'):
            for identity in getattr(state,field):
                if identity not in self.runtime.units:raise ValueError('Unknown end-trait unit identity')
        if (state.player1_evaded and p.main_attacker_unit is None) or (state.player2_evaded and p.main_target_unit is None):
            raise ValueError('Evasion requires its original main participant')
        trial_units,trial_groups,trial_state,trial_p=deepcopy((self.runtime.units,self.runtime.groups,state,p))
        runtime=copy(self.runtime);runtime.units=trial_units;runtime.groups=trial_groups
        calculation=copy(self.calculation);calculation.runtime=runtime
        FixedStageEndTraits(calculation)._execute(trial_state,trial_p)
        return self._execute(state,p)

    def _execute(self,state,p):
        self.events=[];weapon=p.active_attacker_weapon
        self.weapon=None if weapon is None else weapon.base
        execute_end_traits(self,state,p)
        return dict(events=self.events)

    def event(self,identity,timing,p):
        s=self.calculation
        value=self.runtime.battle_end_hit_event(s.context,s.phase,self.runtime.units[identity],timing,p,weapon=self.weapon)
        self.events.append(dict(event='end_hit_event',unit=identity,timing=timing,result=value))

    def evasion(self,identity):
        value=self.runtime.consume_battle_end_action(self.calculation.phase,self.runtime.units[identity],9)
        self.events.append(dict(event='consume_evasion',unit=identity,timing=9,result=value))

    def battle_end(self,units,p):
        s=self.calculation;value=self.runtime.on_battle_end(s.context,s.phase,units,p,weapon=self.weapon)
        self.events.append(dict(event='battle_end',units=[u['id'] for u in units],result=value))

    def always(self,units,p):
        value=self.runtime.execute(self.calculation.context,p,weapon=self.weapon,units=units)
        self.events.append(dict(event='always',units=[u['id'] for u in units],result=value))
