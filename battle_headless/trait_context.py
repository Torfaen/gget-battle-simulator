"""Original trait context assembly and cached origin selection.

Repository, current-step and map state are explicit inputs. This does not
construct an entire battle participation status or infer movement from a unit.
"""
from dataclasses import dataclass
from types import SimpleNamespace
from .battle_sides import is_alive_on_map
from .original import i32
from .battle_participation import BattleParticipationState


@dataclass
class TraitContextStep:
    operating_unit_id: object
    free_move_coord: object
    is_end_step: bool


@dataclass(frozen=True)
class HpTypeCandidate:
    turn: int
    unit: object
    origin: object
    participation: object
    hp: int
    max_hp: int


def hp_type_candidate(turn,unit,origin,participation):
    # TraitConditionHpTypeCheckCandidate snapshots GetBuffedMaxHp and
    # BattleStartHp, even if the current HP changes before its later query.
    return HpTypeCandidate(i32(turn),unit,origin,participation,
        i32(unit['hp'] if unit.get('battle_start_hp') is None else unit['battle_start_hp']),
        i32(unit['max_hp']))


class RepositoryTraitContext:
    """TraitExecutionContext.Reinitialize/GetUnitOriginCoord over services.

    AliveUnits is a list: appending participation units preserves duplicates.
    The coordinate cache is keyed by unit ID and also caches missing origins.
    A supplied candidate list is retained by reference, as in the original.
    """
    def __init__(self,services):
        self.services=services
        self.alive_units=[];self.hp_type_check_candidates=[];self._origin_cache={}

    def reinitialize(self,participation=None,hp_candidates=None):
        s=self.services
        self.turn=s.current_turn()
        self.step=s.current_step(self.turn)
        self.map_root=s.map_root()
        alive=s.alive_units()
        self.alive_units.clear();self.alive_units.extend(alive)
        if participation is not None:self.alive_units.extend(s.participating_units(participation))
        self._origin_cache.clear()
        if hp_candidates is not None:self.hp_type_check_candidates=hp_candidates
        else:
            self.hp_type_check_candidates.clear()
            for unit in self.alive_units:
                if s.unit_side(unit)==7:continue
                origin=self.origin_coord(unit)
                self.hp_type_check_candidates.append(s.hp_candidate(s.turn_number(self.turn),unit,origin,participation))
        return self

    def origin_coord(self,unit):
        identity=unit['id']
        if identity not in self._origin_cache:
            self._origin_cache[identity]=self._calculate_origin(unit)
        return self._origin_cache[identity]

    def _calculate_origin(self,unit):
        # Canonical repository entities have reference identity in the list.
        if not any(candidate is unit for candidate in self.alive_units):return None
        step=self.step
        if step is not None and step.operating_unit_id==unit['id'] and not step.is_end_step:
            return self.services.free_move_coord(step)
        return self.services.find_origin(self.map_root,unit['id'],1)


def build_trait_context(units,*,turn,step,map_origin,participation=None,
                        participating_units=None,hp_candidates=None):
    """Build from explicit fixed-stage repositories and current-step state.

    map_origin(unit_id, place_type) implements the original map lookup. This
    caller passes Default=1; Reinforcement=2 is a separate dictionary lookup,
    not an include-reserve boolean or a fallback search.
    A concrete status supplies its original cache. Legacy snapshots require an
    explicit unit list; role IDs are never expanded through a guessed order.
    """
    def get_participating_units(value):
        if isinstance(value,BattleParticipationState):return value.participants
        if participating_units is None:raise ValueError('Original ordered participation units required')
        return participating_units
    if participation is not None:get_participating_units(participation)
    units=tuple(units)
    def alive_units():
        result=[]
        for unit in units:
            existence=unit.get('existence',unit.get('chance_step',{}).get('existence'))
            if existence is None:raise ValueError('Explicit original unit existence required')
            if is_alive_on_map(existence):result.append(unit)
        return result
    services=SimpleNamespace(current_turn=lambda:turn,current_step=lambda turn:step,map_root=lambda:map_origin,
        alive_units=alive_units,participating_units=get_participating_units,
        unit_side=lambda u:u['battle_side'] if 'battle_side' in u else u['formation_group']['side'],
        turn_number=lambda value:value,free_move_coord=lambda value:value.free_move_coord,
        find_origin=lambda root,identity,place_type:root(identity,place_type),hp_candidate=hp_type_candidate)
    return RepositoryTraitContext(services).reinitialize(participation,hp_candidates)
