"""Original runtime identity/state construction from explicit master sources.

Concrete trait effect creation/activation is separate; normalized states here
feed the verified consumption service without inventing master-based IDs.
"""
from copy import deepcopy
from .original import i32
from .trait_turn_progress import phase
from .turns import PhaseValue


def i64(value):return (int(value)+2**63)%2**64-2**63


def creation_phase(current_phase):
    """Factory fallback when the turn repository/current phase is still empty.

    Initial unit conversion precedes TurnActivityEntity creation. This fallback
    belongs to creation only; event consumption still requires an explicit phase.
    """
    return PhaseValue(0,0) if current_phase is None else phase(current_phase)


def grant_owner_source(detail,source_unit_id):
    """SourceUnitId is an enabled-with-grant-owner link, not general provenance."""
    return source_unit_id if detail['IsEnabledWithGrantOwnerActive'] else None


class RuntimeTraitStateFactory:
    def __init__(self):
        self.trait_counter=self.prediction_trait_counter=0
        self.set_counter=self.prediction_set_counter=0

    def next_trait_id(self,*,prediction=False):
        field='prediction_trait_counter' if prediction else 'trait_counter'
        value=i64(getattr(self,field)+(-1 if prediction else 1))
        setattr(self,field,value);return value

    def next_set_id(self,*,prediction=False):
        field='prediction_set_counter' if prediction else 'set_counter'
        value=i64(getattr(self,field)+(-1 if prediction else 1))
        setattr(self,field,value);return value

    def reset_prediction_ids(self):
        self.prediction_set_counter=self.prediction_trait_counter=0

    def create_set(self,master,master_set_id,owner_unit_id,current_phase,*,
                   source_type,source_id,source_name,source_unit_id=None,
                   additional_limit=0,stack_count=1,additional_trait_value=0,prediction=False):
        if not hasattr(master,'_runtime_trait_state_sources'):
            details={row['TraitSetId']:row for row in master.tables['m_trait_set_detail']}
            values={}
            for link in master.tables['m_trait_set']:
                values.setdefault(link['Id'],[]).append(master.index['m_trait'][link['TraitId']])
            master._runtime_trait_state_sources=(details,values)
        details,values=master._runtime_trait_state_sources
        detail=details[master_set_id];definitions=values.get(master_set_id,[])
        created=creation_phase(current_phase)
        source_unit_id=grant_owner_source(detail,source_unit_id)
        set_id=self.next_set_id(prediction=prediction)
        result=dict(runtime_set_id=set_id,master_trait_set_id=master_set_id,
            owner_unit_id=owner_unit_id,source_type=source_type,source_id=source_id,
            source_name=source_name,source_unit_id=source_unit_id,
            created_phase_value=created,turn_progress_consumed_phase_value=created,
            enabled_internal=True,enabled_while_all_traits_usable=detail['IsEnabledWhileAllTraitsUsable'],
            master_detail=deepcopy(detail),stack_count=i32(stack_count),values=[],prediction=prediction,
            effect_registration_complete=not definitions)
        for row in definitions:
            result['values'].append(dict(runtime_trait_id=self.next_trait_id(prediction=prediction),
                parent_set_id=set_id,master_trait_id=row['Id'],owner_unit_id=owner_unit_id,
                source_type=source_type,source_id=source_id,source_unit_id=source_unit_id,
                trait_type=row['TraitTypeIndex'],trait_value=i32(row['TraitValue']*i32(stack_count)+additional_trait_value),
                stack_count=i32(stack_count),additional_trait_value=i32(additional_trait_value),
                action_timing=row['ActionTimingTypeIndex'],base_limit=i32(row['Limit']),
                additional_limit=i32(additional_limit),used_count=0,master_trait=deepcopy(row)))
        return result
