"""Post-chance removal queue and later ordinary map/ADV cleanup boundary.

Queueing and applying are deliberately separate: original ExecuteAsync queues;
_ReflectViewAsync later removes map occupancy, clears ADV battle context, saves
that context and clears the queue. Intervening events and later Always/step
updates remain the caller's responsibility. Unit repository entities survive.
"""
from copy import copy,deepcopy
from dataclasses import dataclass,field
from .original import SOURCE_MASTER_SHA256,i32
from .battle_user_records import entity_is_alive,unit_existence
from .occupancy import UnitFootprint


@dataclass
class StepRemovalQueue:
    unit_ids: list=field(default_factory=list)


@dataclass
class AdvBattleContext:
    attacker_id: object=None
    participant_unit_ids: set=field(default_factory=set)
    saves: int=0

    def snapshot(self):
        return dict(attacker_id=self.attacker_id,participant_unit_ids=sorted(self.participant_unit_ids),saves=self.saves)


class SharedMapOccupancy:
    """Explicit original dictionaries plus current-form footprint expansion.

    Initial dictionaries are supplied snapshots, not an implementation of the
    global map constructor. Removal erases cells by coordinate, with no check
    that a cell's stored occupant matches the unit being removed.
    """
    def __init__(self,units,footprints,origins,places,*,reinforcements=None,geometry=None):
        self.units=units;self.footprints=dict(footprints)
        self.unit_specs={k:u.get('unit_id') for k,u in units.items()}
        self.origins={k:tuple(v) for k,v in origins.items()}
        self.places={tuple(k):v for k,v in places.items()}
        self.reinforcements={k:tuple(v) for k,v in (reinforcements or {}).items()};self.geometry=geometry

    @classmethod
    def from_fixed_initial_units(cls,calculation):
        runtime=calculation.runtime;master=runtime.master;units=runtime.units
        if master.sha256!=SOURCE_MASTER_SHA256 or len(units)!=22 or {u['native_unit_id'] for u in units.values()}!=set(range(1,23)):
            raise ValueError('Complete pinned initial roster required for map snapshot')
        footprints={};origins={};places={}
        for key,u in units.items():
            if key!=u['id'] or not entity_is_alive(unit_existence(u)):
                raise ValueError('Canonical living initial units required')
            source=master.index['m_unit'][u['unit_id']]
            footprint=UnitFootprint.from_row(master.index['m_occupied_area'][source['OccupiedAreaId']])
            footprints[key]=footprint;origins[key]=tuple(u['position'])
            for coord in footprint.ordered_coords(origins[key],u['direction']):
                if coord in places:raise ValueError('Overlapping initial map snapshot')
                places[coord]=key
        return cls(units,footprints,origins,places,geometry=calculation.map.geometry)

    def bind(self,calculation):
        if self.units is not calculation.runtime.units:raise ValueError('Shared canonical map units required')
        calculation.map=self;calculation._context_origin=self.find_origin
        return self

    def find_origin(self,identity,place_type=1):
        return (self.reinforcements if i32(place_type)==2 else self.origins).get(identity)

    def ordered_occupied(self,unit,position=None):
        key=unit['id']
        if unit.get('unit_id')!=self.unit_specs[key]:raise NotImplementedError('Refresh current-form footprint before map mutation')
        if position is None:position=self.find_origin(key)
        if position is None:raise ValueError('Map origin required for footprint expansion')
        return self.footprints[key].ordered_coords(position,unit['direction'])

    def occupied(self,unit,position=None):return set(self.ordered_occupied(unit,position))

    def remove_unit(self,unit,events):
        key=unit['id'];origin=self.find_origin(key,1);events.append(['origin',key,1])
        if origin is None:return
        coords=self.ordered_occupied(unit,origin);events.append(['expand',key,list(origin),0])
        for coord in coords:
            self.places.pop(coord,None);events.append(['remove_place',list(coord)])
        self.origins.pop(key,None);events.append(['remove_origin',key])

    def snapshot(self):
        return dict(origins=[dict(id=k,coord=list(v)) for k,v in self.origins.items()],
            reinforcements=[dict(id=k,coord=list(v)) for k,v in self.reinforcements.items()],
            places=[dict(coord=list(k),id=v) for k,v in self.places.items()])


def queue_non_alive_participants(services,participants):
    for unit in participants:
        if not services.alive(unit):services.enqueue(unit['id'])


def apply_pending_map_removal(services):
    for identity in services.queue.unit_ids:services.remove(services.find(identity))
    services.clear_adv();services.clear_queue()


class FixedStageRemoval:
    def __init__(self,calculation,map_state,queue,adv):
        if map_state.units is not calculation.runtime.units or calculation.map is not map_state:
            raise ValueError('Bind the shared map before ordinary removal')
        self.calculation=calculation;self.map=map_state;self.queue=queue;self.adv=adv;self.events=[]

    def queue_after_battle(self,participation):
        units=self.calculation.runtime.units
        for u in participation.participants:
            if u is None or units.get(u['id']) is not u:raise ValueError('Canonical cached removal participants required')
            entity_is_alive(unit_existence(u))
        self.events=[];queue_non_alive_participants(self,participation.participants)
        return self.snapshot()

    def apply_pending(self):
        # This boundary only mutates map dictionaries, queue and ADV context.
        trial=copy(self);trial.map=copy(self.map)
        trial.map.origins,trial.map.places,trial.map.reinforcements=deepcopy((self.map.origins,self.map.places,self.map.reinforcements))
        trial.queue,trial.adv=deepcopy((self.queue,self.adv));trial.events=[]
        apply_pending_map_removal(trial)
        self.events=[];apply_pending_map_removal(self)
        return self.snapshot()

    def alive(self,u):
        self.events.append(['alive',u['id']]);return entity_is_alive(unit_existence(u))
    def enqueue(self,identity):self.queue.unit_ids.append(identity);self.events.append(['enqueue',identity])
    def find(self,identity):
        self.events.append(['find',identity]);return self.calculation.runtime.units[identity]
    def remove(self,u):self.map.remove_unit(u,self.events)
    def clear_adv(self):
        self.adv.attacker_id=None;self.adv.participant_unit_ids.clear();self.events.append(['clear_adv'])
        self.adv.saves+=1;self.events.append(['save_adv'])
    def clear_queue(self):self.queue.unit_ids.clear();self.events.append(['clear_queue'])
    def snapshot(self):
        return dict(events=deepcopy(self.events),queue=list(self.queue.unit_ids),map=self.map.snapshot(),adv=self.adv.snapshot())
