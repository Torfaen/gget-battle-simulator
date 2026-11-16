"""Shared defeated-event stage; caller must disable weapons and resolve escape first."""
from copy import copy,deepcopy


class FixedStageUnitDefeated:
    def __init__(self,calculation):self.calculation=calculation;self.runtime=calculation.runtime

    def execute(self,participation):
        units=tuple(participation.participants)
        if any(self.runtime.units.get(u['id']) is not u for u in units):
            raise ValueError('Canonical defeated-event participants required')
        trial_units,trial_groups,trial_targets=deepcopy((self.runtime.units,self.runtime.groups,units))
        runtime=copy(self.runtime);runtime.units=trial_units;runtime.groups=trial_groups
        calculation=copy(self.calculation);calculation.runtime=runtime
        runtime.on_unit_defeated(calculation.context,calculation.phase,trial_targets)
        return self.runtime.on_unit_defeated(self.calculation.context,self.calculation.phase,units)
