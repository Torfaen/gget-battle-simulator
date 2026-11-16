"""Connect recovered footprints/range geometry to the flat-grid executor.

Movement remains a uniform-cost four-neighbor scheduler with fixed facing.
Terrain costs, turning actions and stage-driven region opening are not inferred.
"""
from .unit_life import is_alive
from collections import deque
from functools import lru_cache
from .geometry import Area, MapGeometry, around_range
from .occupancy import UnitFootprint


@lru_cache(maxsize=4096)
def _range(area, minimum, maximum):
    return frozenset(around_range(area, minimum, maximum))


class BattleMap:
    def __init__(self, env, footprints, geometry=None):
        self.env = env
        self.footprints = dict(footprints)
        self.geometry = geometry or MapGeometry.from_dimensions(env.width, env.height)

    def occupied(self, unit, position=None):
        return self.footprints[unit['id']].coords(
            unit['position'] if position is None else position, unit['direction'])

    def ordered_occupied(self,unit,position=None):
        return self.footprints[unit['id']].ordered_coords(
            unit['position'] if position is None else position,unit['direction'])

    def distance(self,attacker,target):
        from .geometry import place_set_distance
        return place_set_distance(self.occupied(attacker),self.occupied(target))

    def within_range(self, attacker, target, weapon):
        area = Area.from_coords(self.occupied(attacker))
        places = _range(area, weapon['range_min'], weapon['range_max'])
        return any(p in places and self.geometry.exists(p) for p in self.occupied(target))

    def can_place(self, unit, position, other_occupied=None):
        if other_occupied is None:
            other_occupied = set().union(*(self.occupied(u) for u in self.env.units.values()
                if u['id'] != unit['id'] and is_alive(u)))
        cells = self.occupied(unit, position)
        return (not cells.intersection(other_occupied | self.env.blocked)
                and all(self.geometry.exists(p) for p in cells))

    def _reachable_origins(self, unit, *, allow_friendly_overlap=False, movement=None):
        others = set().union(*(self.occupied(u) for u in self.env.units.values()
            if u['id'] != unit['id'] and is_alive(u)
            and not (allow_friendly_overlap and u['team'] == unit['team'])))
        start = tuple(unit['position'])
        distances, queue = {start: 0}, deque([start])
        while queue:
            x, y = queue.popleft()
            if distances[x, y] >= (unit['movement'] if movement is None else movement):
                continue
            for p in ((x-1, y), (x+1, y), (x, y-1), (x, y+1)):
                if p not in distances and self.can_place(unit, p, others):
                    distances[p] = distances[x, y] + 1
                    queue.append(p)
        return distances

    def reachable(self, unit):
        from .action_constraints import movement_blocked
        if movement_blocked(unit):return []
        start = tuple(unit['position'])
        return sorted(p for p in self._reachable_origins(unit) if p != start)

    def support_defend_coords(self, unit):
        correction = unit.get('scalar_corrections', {}).get('SupportDefendDistanceValue', 0)
        origins = self._reachable_origins(unit, allow_friendly_overlap=True,
                                           movement=max(0, unit['movement'] + correction))
        return set().union(*(self.occupied(unit, origin) for origin in origins))


def attach_battle_map(env, stage, *, geometry=None):
    if geometry is None and stage.source.get('m_map_stage_exclude_area'):
        raise NotImplementedError('Supply explicit exclusion geometry and open states')
    placements = {n['placement']['Id']: n['placement'] for n in stage.npcs}
    anchors = stage.source.get('m_map_stage_group_initial_placement', [])
    footprints = {}
    for uid, unit in env.units.items():
        definition = stage.master.index['m_unit'][unit['unit_id']]
        row = stage.master.index['m_occupied_area'][definition['OccupiedAreaId']]
        footprints[uid] = UnitFootprint.from_row(row)
        if 'direction' not in unit:
            if 'source_npc_id' in unit and unit['team'] != 'player':
                unit['direction'] = placements[unit['source_npc_id']]['DirectionTypeIndex']
            elif 'account_configuration' in unit:
                force = unit['account_configuration']['force']
                unit['direction'] = next(a['DirectionTypeIndex'] for a in anchors
                    if a['BattleSidePlacedTypeIndex'] == force)
            else:
                unit['direction'] = 2
    battle_map = BattleMap(env, footprints, geometry)
    # Do not silently accept deployment overlaps now that footprints matter.
    for unit in env.units.values():
        if is_alive(unit) and not battle_map.can_place(unit, unit['position']):
            raise ValueError('Invalid occupied-area deployment: ' + unit['id'])
    env.battle_map = battle_map
    env._map_context_stage=stage
    env.rules._battle_distance = battle_map.distance
    env._reachable = battle_map.reachable
    env._within_range = battle_map.within_range
    from .map_targets import attach_map_targeting
    attach_map_targeting(env)
    return battle_map
