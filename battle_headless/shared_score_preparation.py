"""Fresh fixed-roster stage traits, score snapshot, then first-turn traits.

Receives already constructed canonical units/map/calculation services. It does
not implement the original entity constructor, ADV/stage-event scheduling,
saved-game/undo restoration, or attach the experimental CLI's live repository.
The caller must finish preparation before invoking begin_first_turn.
"""
from copy import deepcopy
from dataclasses import asdict
from .battle_user_records import unit_side,unit_existence
from .npc_battle_power import fixed_npc_battle_powers
from .ordinary_movement_score import BattleScoreService
from .score_preparation import score_configuration
from .original import SOURCE_MASTER_SHA256,i32


class FixedStageFreshScorePreparation:
    def __init__(self,calculation,stage,identities):
        runtime=calculation.runtime
        if stage.stage_id!=90510005 or runtime.master.sha256!=SOURCE_MASTER_SHA256 or stage.master.sha256!=runtime.master.sha256:
            raise ValueError('Pinned stage and shared master required')
        if set(identities)!=set(runtime.units):raise ValueError('Complete original unit identity mapping required')
        mapping={key:row['native_unit_id'] for key,row in identities.items()}
        if len(mapping)!=22 or set(mapping.values())!=set(range(1,23)):
            raise ValueError('Fixed original identity creation stream required')
        npc_sources={row['npc_id']:row for row in fixed_npc_battle_powers(stage)};npc_bindings={}
        for key,u in runtime.units.items():
            if key!=u['id'] or u['native_unit_id']!=mapping[key]:raise ValueError('Canonical original unit identity mismatch')
            source=u.get('source_npc_id')
            if source is not None:
                if source not in npc_sources or source in npc_bindings:raise ValueError('Unique initial NPC source required')
                if i32(unit_side(u))!=npc_sources[source]['side']:raise ValueError('Initial NPC side changed before preparation')
                if identities[key].get('source_npc_id')!=source:raise ValueError('NPC identity provenance mismatch')
                npc_bindings[source]=key
            elif i32(unit_side(u))!=0:raise ValueError('Fixed own formation required')
        if set(npc_bindings)!=set(npc_sources):raise ValueError('Complete initial NPC sources required')
        self.calculation=calculation;self.stage=stage;self.identities=mapping
        self.npc_sources=npc_sources;self.npc_bindings=npc_bindings
        self._canonical_units=tuple(runtime.units.items())
        self.score_service=BattleScoreService(calculation,None,initialized=False)
        self.prepared=False;self.first_turn_started=False;self.events=[];self.preparation_snapshot=None

    def _check_context(self):
        runtime=self.calculation.runtime
        if len(runtime.units)!=len(self._canonical_units) or any(runtime.units.get(key) is not u
                or u['id']!=key or u['native_unit_id']!=self.identities[key] for key,u in self._canonical_units):
            raise ValueError('Preparation canonical identity mapping changed')
        for source,key in self.npc_bindings.items():
            u=runtime.units[key]
            if u.get('source_npc_id')!=source or i32(unit_side(u))!=self.npc_sources[source]['side']:
                raise ValueError('Initial NPC binding changed before preparation')
        if self.calculation.step is not None:raise ValueError('Preparation requires the original null step context')
        if (self.calculation.phase.turn,self.calculation.phase.id)!=(1,0):
            raise ValueError('Fresh first own phase required')

    def initialize(self):
        if self.prepared:raise ValueError('Fresh preparation already completed')
        self._check_context();runtime=self.calculation.runtime
        if any(u['runtime_trait_effects'] or u['combat_traits'] for u in runtime.units.values()):
            raise ValueError('Fresh pre-passive canonical units required')
        stage_result=runtime.on_stage_start(self.calculation.context,self.calculation.phase)
        self.events.append('stage_start')
        rows=[]
        for key,u in runtime.units.items():
            source=self.npc_sources.get(u.get('source_npc_id'))
            row=dict(id=key,native_unit_id=self.identities[key],side=i32(unit_side(u)),existence=unit_existence(u),
                # Own AdvOnly is never read by the original preparation path.
                adv_only=False if source is None else source['adv_only'],
                buffed_max_hp=self.calculation.max_hp(u))
            if source is not None:row.update(source_npc_id=source['npc_id'],power=source['output']['power'])
            rows.append(row)
        self.score_service.initialize_calculator(rows,score_configuration(self.stage),
            battle_power=lambda u:u['power'],initial_max_hp=lambda u:u['buffed_max_hp'])
        self.events.append('score_initialize');self.prepared=True
        self.preparation_snapshot=dict(units=deepcopy(rows),calculator=asdict(self.score_service.calculator),
            native_enemy_battle_power={self.identities[key]:value for key,value in self.score_service.calculator.enemy_battle_power.items()},
            stage_start=deepcopy(stage_result))
        return deepcopy(self.preparation_snapshot)

    def begin_first_turn(self):
        if not self.prepared:raise ValueError('Score preparation must precede first-turn effects')
        if self.first_turn_started:raise ValueError('First-turn effects already executed')
        self._check_context()
        result=self.calculation.runtime.on_first_turn_start(self.calculation.context,self.calculation.phase)
        self.events.append('first_turn_start');self.first_turn_started=True
        return result
