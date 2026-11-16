"""Explicit post-end-trait regeneration phase of the ordinary exchange.

Weapon trait disable, defeat cleanup and resource reflection follow this
phase. It does not perform those stages or decide strike eligibility.
"""
from copy import copy,deepcopy


def execute_regeneration(services,state,participation):
    owners=dict.fromkeys((*state.player1_damaged,*state.player2_damaged))
    results=[]
    for identity in owners:
        result=services.regeneration(identity,participation)
        # Original helper collects only nonnull visual results; a nonempty
        # demand result list produces a DTO even when no effect was effective.
        if result['on_demand']['results']:results.append(dict(unit_id=identity,result=result))
    return dict(owner_ids=list(owners),results=results)


class FixedStageRegeneration:
    """Shared phase using original condition/demand/consumption services.

Preflights all owners together so an unsupported later source leaves no
earlier recovery/consumption. This is not original exception-path emulation.
"""
    def __init__(self,calculation):self.calculation=calculation;self.runtime=calculation.runtime;self.events=[]

    def execute(self,state,participation):
        p=participation
        units=[*p.participants,p.main_attacker_unit,p.main_target_unit,p.support_defender_unit,
               p.attack_side_support_defender_unit,*p.support_attack_units,*p.counter_support_units]
        if any(u is not None and self.runtime.units.get(u['id']) is not u for u in units):
            raise ValueError('Canonical regeneration participants required')
        for identity in (*state.player1_damaged,*state.player2_damaged):
            if identity not in self.runtime.units:raise ValueError('Unknown regeneration owner identity')
        trial_units,trial_groups,trial_state,trial_p=deepcopy((self.runtime.units,self.runtime.groups,state,p))
        runtime=copy(self.runtime);runtime.units=trial_units;runtime.groups=trial_groups
        calculation=copy(self.calculation);calculation.runtime=runtime
        FixedStageRegeneration(calculation)._execute(trial_state,trial_p)
        return self._execute(state,p)

    def _execute(self,state,p):
        self.events=[];weapon=p.active_attacker_weapon
        self.weapon=None if weapon is None else weapon.base
        result=execute_regeneration(self,state,p);result['events']=self.events
        return result

    def regeneration(self,identity,p):
        s=self.calculation
        result=self.runtime.on_regeneration(s.context,s.phase,self.runtime.units[identity],p,weapon=self.weapon)
        self.events.append(dict(event='regeneration',unit=identity,result=result))
        return result
