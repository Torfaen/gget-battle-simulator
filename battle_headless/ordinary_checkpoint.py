"""Ordinary post-removal snapshot assembly and the latched victory exit gate.

The container/reference assembly follows IngameMementoManager. Unit conversion,
supporter conversion and storage are explicit boundaries: Python diagnostics
are not the client's MemoryPack byte format or a resume/undo implementation.
The caller's progress projection and voice cleanup remain presentation ports.
"""
from copy import deepcopy
from dataclasses import dataclass,fields,is_dataclass
import hashlib
import json
from .original import SOURCE_LIB_SHA256
from .trait_factory import i64


@dataclass
class LatchedVictoryConditions:
    exists: bool
    success: bool
    failure: bool

    def is_finished(self):return bool(self.success or self.failure)
    def should_be_checked(self):return bool(self.exists and not self.success and not self.failure)


@dataclass
class SnapshotContext:
    stage_id: object
    victory_conditions: LatchedVictoryConditions
    is_sortie_from_invasion_restriction_normal_mode: bool
    state: object=None


@dataclass
class StepMemoryPack:
    unit_list: list
    turn_list: object
    map_root: object
    context: object
    trait_set_list: object
    adv_context: object
    battle_score: object
    supporter_list: object
    master_league_battle_info: object=None
    chronicle_event_battle_info: object=None


@dataclass
class SnapshotRepositories:
    units: object
    unit_save_data: object
    turns: object
    map_root: object
    context: SnapshotContext
    traits: object
    adv: object
    battle_score: object
    supporter_save_data: object


@dataclass
class PendingStepOperations:
    initial_units: object
    form_change_result: object=None
    unit_move_data: object=None
    battle_calculate_result: object=None
    map_weapon_input_data: object=None
    map_weapon_calculate_result: object=None
    character_skill_input_data: object=None
    supporter_active_skill_input_data: object=None
    step_operations: object=None
    form_change_unit_data: object=None
    unit_skill_execution: object=None


class StepMementoManager:
    def __init__(self,repositories,operations,store,*,master_url,stage_total_start_count):
        self.repositories=repositories;self.operations=operations;self.store=store
        self.master_url=master_url;self.stage_total_start_count=stage_total_start_count
        self.latest_step=None;self.latest_turn=None;self.events=[]

    def create_ingame_memory_pack(self):
        r=self.repositories
        self.events.append('units')
        units=[r.unit_save_data(u) for u in r.units]
        self.events.extend(('turns','map','context','traits','adv','score','supporters'))
        return StepMemoryPack(units,r.turns,r.map_root,r.context,r.traits,r.adv,r.battle_score,r.supporter_save_data())

    def create_all_memory_pack(self):
        r=self.repositories;o=self.operations
        self.events.extend(('user_sortie_stage','context_for_save'))
        # LatestTurn and live commentary lottery result are not ctor arguments.
        return dict(master_url=self.master_url,stage_total_start_count=0 if self.stage_total_start_count is None
                    else i64(self.stage_total_start_count),stage_id=r.context.stage_id,
            initial_units=o.initial_units,latest_step=self.latest_step,form_change_result=o.form_change_result,
            unit_move_data=o.unit_move_data,battle_calculate_result=o.battle_calculate_result,
            map_weapon_input_data=o.map_weapon_input_data,map_weapon_calculate_result=o.map_weapon_calculate_result,
            character_skill_input_data=o.character_skill_input_data,supporter_active_skill_input_data=o.supporter_active_skill_input_data,
            step_operations=o.step_operations,master_league_battle_info=None,form_change_unit_data=o.form_change_unit_data,
            unit_skill_execution=o.unit_skill_execution,
            is_sortie_from_invasion_restriction_normal_mode=bool(r.context.is_sortie_from_invasion_restriction_normal_mode))

    def save_latest_step_start_snapshot(self,name=None):
        # Original accepts a label but does not read it on this method's path.
        self.events=[]
        self.latest_step=self.create_ingame_memory_pack();self.events.append('latest_step')
        data=self.create_all_memory_pack();self.store.save(data);self.events.append('save')
        return self.latest_step


class SemanticSnapshotStore:
    """Freeze represented Python state at Save, with an optional diagnostic projection.

    This boundary is intentionally separate from original object assembly.
    Projection must reject unhandled objects instead of silently omitting them.
    """
    def __init__(self,project=deepcopy):self.project=project;self.saved=[]
    def save(self,data):self.saved.append(self.project(data))


class OrdinaryStepCheckpoint:
    def __init__(self,memento,*,get_progress,publish_progress,unload_voice):
        self.memento=memento;self.get_progress=get_progress;self.publish_progress=publish_progress
        self.unload_voice=unload_voice;self.events=[]

    def execute(self,participation):
        self.events=[]
        self.memento.save_latest_step_start_snapshot();self.events.append('snapshot')
        progress=self.get_progress();self.events.append('progress')
        self.publish_progress(progress);self.events.append('publish_progress')
        self.events.append('finished')
        finished=self.memento.repositories.context.victory_conditions.is_finished()
        if finished:
            self.unload_voice(participation);self.events.append('unload_voice')
        return finished


def shared_snapshot_repositories(calculation,removal,*,turns,context,battle_score,supporters):
    """Concrete adapter for the existing fixed-roster Python repositories.

    Unit conversion freezes every represented unit field. This deliberately
    records more Python state than the original per-unit save DTO, whose binary
    conversion is a separate unverified boundary. Map/context/traits/ADV/score
    retain references until the store projects them, matching outer assembly.
    The caller supplies turn/context/supporter state rather than guessed defaults.
    """
    if removal.calculation is not calculation or calculation.map is not removal.map:
        raise ValueError('Shared canonical map/removal repositories required')
    if removal.queue.unit_ids:raise ValueError('Apply the pending removal queue before capturing this boundary')
    return SnapshotRepositories(calculation.runtime.units.values(),deepcopy,turns,removal.map,context,
        calculation.runtime.groups,removal.adv,battle_score,lambda:supporters)


def diagnostic_state(value):
    """Loss-explicit JSON projection of represented state, not a save decoder."""
    from .ordinary_removal import SharedMapOccupancy,AdvBattleContext
    if value is None or type(value) in (bool,int,float,str):return value
    if callable(value):return {'callback':value.__module__+'.'+value.__qualname__}
    if isinstance(value,(SharedMapOccupancy,AdvBattleContext)):return diagnostic_state(value.snapshot())
    if is_dataclass(value) and not isinstance(value,type):
        return {f.name:diagnostic_state(getattr(value,f.name)) for f in fields(value)}
    if isinstance(value,dict):
        if all(isinstance(k,str) for k in value):return {k:diagnostic_state(v) for k,v in value.items()}
        return {'mapping_items':[[diagnostic_state(k),diagnostic_state(v)] for k,v in value.items()]}
    if isinstance(value,list):return [diagnostic_state(v) for v in value]
    if isinstance(value,tuple):return {'tuple':[diagnostic_state(v) for v in value]}
    if isinstance(value,(set,frozenset)):
        rows=[diagnostic_state(v) for v in value]
        return {'set':sorted(rows,key=lambda row:json.dumps(row,sort_keys=True,ensure_ascii=False,allow_nan=False))}
    raise TypeError('Unsupported checkpoint state: '+type(value).__qualname__)


class SharedCheckpointRecorder(SemanticSnapshotStore):
    """Freeze current shared Python state and RNG provenance at the Save boundary.

    Extra records are mandatory explicit references. Their schema is the
    caller's responsibility, so an empty dictionary must not imply that missing
    turn/event/supporter systems have been implemented. Records are diagnostics;
    no loader or native MemoryPack byte compatibility is claimed.
    """
    def __init__(self,calculation,removal,*,extra_records):
        if removal.calculation is not calculation:raise ValueError('Shared checkpoint calculation required')
        self.calculation=calculation;self.removal=removal;self.extra_records=extra_records
        super().__init__(self.capture)

    def capture(self,data):
        s=self.calculation;r=self.removal;factory=s.passives.factory
        payload=dict(schema='battle_headless.shared_checkpoint.v1',libil2cpp_sha256=SOURCE_LIB_SHA256,
            master_sha256=s.runtime.master.sha256,memory=diagnostic_state(data),
            phase=diagnostic_state(s.phase),step=diagnostic_state(s.step),
            non_unit_owners=diagnostic_state(s.runtime.non_unit_owners),
            trait_counters=dict(trait_set=factory.set_counter,trait=factory.trait_counter),
            pending_removal_ids=diagnostic_state(r.queue.unit_ids),extra_records=diagnostic_state(self.extra_records),
            random=dict(state=diagnostic_state(s.rng.getstate()),calls=diagnostic_state(s.rng.calls),draws=diagnostic_state(s.rng.draws)),
            exact_gameplay=False,memorypack_binary_compatible=False,restore_verified=False)
        encoded=json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf8')
        return dict(sha256=hashlib.sha256(encoded).hexdigest(),state=payload)
