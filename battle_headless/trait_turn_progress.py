"""Original standard and master-league turn-progress specifications."""
from .turns import PhaseValue
from .original import i32


def phase(value):
    if isinstance(value,PhaseValue):return PhaseValue(i32(value.turn),i32(value.id))
    if value is None:raise NotImplementedError('Original phase value required')
    return PhaseValue(i32(value['turn']),i32(value['id']))


def standard_satisfies(trait_set,current):
    previous=phase(trait_set['turn_progress_consumed_phase_value'])
    return previous.is_turn_progressed(phase(current))


def master_league_satisfies(trait_set,current,dead_unit_ids_provider):
    created=phase(trait_set['created_phase_value']);current_phase=phase(current)
    if created.is_turn_progressed(current_phase):return True
    if dead_unit_ids_provider is None:
        raise NotImplementedError('Original master-league dead-unit interval service required')
    ids=dead_unit_ids_provider(current)
    return any(created.is_turn_progressed(PhaseValue(current_phase.turn,i32(identity))) for identity in (ids or ()))
