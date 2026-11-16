from .unit_life import is_alive
from collections import deque
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import random

from battle_paths import MASTER_SNAPSHOT

DEFAULT_MASTER = MASTER_SNAPSHOT / 'parsed_tables.json'


class MasterData:
    def __init__(self, path=DEFAULT_MASTER):
        self.path = Path(path)
        raw = self.path.read_bytes()
        self.sha256 = hashlib.sha256(raw).hexdigest()
        self.tables = json.loads(raw)
        self.index = {name: {r['Id']: r for r in rows if 'Id' in r}
                      for name, rows in self.tables.items()}

    def unit(self, unit_id, instance, team, position):
        row = self.index['m_unit'][unit_id]
        stats = self.index['m_unit_status'][row['UnitStatusId']]
        weapons = []
        for link in self.tables['m_unit_weapon']:
            if link['UnitId'] != unit_id:
                continue
            weapon = self.index['m_weapon'][link['WeaponId']]
            # Enum meanings are not assumed: exclude every explicit MAP shape.
            status = self.index['m_weapon_status'][weapon['WeaponStatusId']]
            if (weapon['MapWeaponRangeTypeIndex'] or status['MapWeaponShootingRange']
                    or status['MapWeaponEffectRange']):
                continue
            weapons.append(dict(id=weapon['Id'], power=status['Power'],
                                en=status['En'], hit=status['HitRate'],
                                critical=status['CriticalRate'],
                                range_min=status['RangeMin'], range_max=status['RangeMax']))
        return dict(id=instance, unit_id=unit_id, team=team, position=list(position),
                    hp=stats['Hp'], max_hp=stats['Hp'], en=stats['En'],
                    max_en=stats['En'], attack=stats['Attack'], defense=stats['Defense'],
                    mobility=stats['Mobility'], movement=stats['Movement'],
                    weapons=weapons, moved=False, acted=False)


@dataclass(frozen=True)
class ApproximateRules:
    """Placeholder formula, NOT recovered game logic. Replace after calibration."""
    critical_multiplier: float = 1.5

    def damage(self, attacker, defender, weapon, critical):
        base = weapon['power'] * attacker['attack'] / max(1, defender['defense'])
        return max(1, int(base * (self.critical_multiplier if critical else 1)))


class BattleEnv:
    """JSON-compatible reset/observe/legal_actions/step API. No emulator required.

    The demo defaults to alternating teams and a flat grid. Original-profile
    adapters supply standard phase progression, scalar combat, traits and
    chance steps. Full counters, skills and terrain services remain external.
    """
    def __init__(self, master=None, rules=None):
        self.master = master or MasterData()
        self.rules = rules or ApproximateRules()
        self.ready = False

    def reset(self, scenario, seed=0):
        scenario = deepcopy(scenario)
        width, height = scenario['width'], scenario['height']
        if type(width) is not int or type(height) is not int or min(width, height) < 1:
            raise ValueError('Grid dimensions must be positive integers')
        self.width, self.height = width, height
        def valid_pos(p):
            return (len(p) == 2 and all(type(v) is int for v in p)
                    and 0 <= p[0] < width and 0 <= p[1] < height)
        blocked = scenario.get('blocked', [])
        if any(not valid_pos(p) for p in blocked):
            raise ValueError('Invalid blocked cell')
        units = {}
        occupied = {tuple(p) for p in blocked}
        for spec in scenario['units']:
            key, pos = spec['id'], spec['position']
            if not isinstance(key, str) or not key or key in units:
                raise ValueError('Instance IDs must be unique nonempty strings')
            if not valid_pos(pos) or tuple(pos) in occupied:
                raise ValueError('Invalid, blocked or overlapping position')
            if not isinstance(spec['team'], str) or not spec['team']:
                raise ValueError('Team must be a nonempty string')
            units[key] = self.master.unit(spec['unit_id'], key, spec['team'], pos)
            occupied.add(tuple(pos))
        teams = list(dict.fromkeys(u['team'] for u in units.values()))
        if len(teams) != 2:
            raise ValueError('Exactly two teams required')
        max_turns = scenario.get('max_turns', 100)
        if type(max_turns) is not int or max_turns < 1:
            raise ValueError('max_turns must be a positive integer')
        self.scenario, self.units, self.teams = scenario, units, teams
        self.moment_trigger=deepcopy(scenario.get('moment_trigger',
            dict(exists=False,is_success=False,is_failure=False)))
        self.blocked = {tuple(p) for p in blocked}
        self.rng, self.seed = random.Random(seed), seed
        self.turn, self.team_index, self.max_turns = 1, 0, max_turns
        self.phase_clock=None
        if scenario.get('phase_clock')=='original-standard':
            from .turns import OriginalTurnClock
            self.phase_clock=OriginalTurnClock()
            self.phase_teams={int(side):team for side,team in scenario['phase_teams'].items()}
            if 0 not in self.phase_teams or set(self.phase_teams.values())!=set(teams):
                raise ValueError('Original clock requires explicit team mapping including Own')
            if set(self.phase_teams)-{0,2,3,4,5,6}:
                raise ValueError('Original standard clock requires canonical phase sides')
        self.terminated = self.truncated = False
        self.winner, self.events = None, []
        self.ready = True
        return self.observe()

    @property
    def active_team(self):
        if self.phase_clock is not None:return self.phase_teams.get(self.phase_clock.side,'phase:'+str(self.phase_clock.side))
        return self.teams[self.team_index]

    def observe(self):
        if not self.ready:
            raise RuntimeError('Call reset first')
        return deepcopy(dict(turn=self.turn, active_team=self.active_team,
                             moment_trigger=self.moment_trigger,
                             phase_clock=(dict(kind='original-standard',side=self.phase_clock.side,
                                transitions=self.phase_clock.transitions) if self.phase_clock else dict(kind='experimental')),
                             width=self.width, height=self.height,
                             blocked=sorted(map(list, self.blocked)),
                             units=list(self.units.values()), terminated=self.terminated,
                             truncated=self.truncated, winner=self.winner,
                             rules=getattr(self.rules,'name','approximate-v1'), master_sha256=self.master.sha256))

    def reachable(self, unit):
        from .action_constraints import movement_blocked
        if movement_blocked(unit):return []
        if hasattr(self, '_reachable'):return self._reachable(unit)
        occupied = self.blocked | {tuple(u['position']) for u in self.units.values()
                                  if is_alive(u) and u['id'] != unit['id']}
        start = tuple(unit['position'])
        distance, queue = {start: 0}, deque([start])
        while queue:
            x, y = queue.popleft()
            if distance[x, y] >= unit['movement']:
                continue
            for p in ((x-1, y), (x+1, y), (x, y-1), (x, y+1)):
                if (0 <= p[0] < self.width and 0 <= p[1] < self.height
                        and p not in occupied and p not in distance):
                    distance[p] = distance[x, y] + 1
                    queue.append(p)
        return sorted(p for p in distance if p != start)

    def within_range(self, attacker, target, weapon):
        if hasattr(self, '_within_range'):return self._within_range(attacker, target, weapon)
        distance = sum(abs(a-b) for a, b in zip(attacker['position'], target['position']))
        return weapon['range_min'] <= distance <= weapon['range_max']

    def legal_actions(self):
        if not self.ready:
            raise RuntimeError('Call reset first')
        if self.terminated or self.truncated:
            return []
        if hasattr(self,'_refresh_state'):self._refresh_state()
        actions = []
        for u in self.units.values():
            if not is_alive(u) or u['team'] != self.active_team or u['acted']:
                continue
            from .action_constraints import movement_blocked
            if not u['moved'] and not movement_blocked(u):
                actions.extend(dict(type='move', unit=u['id'], to=list(p))
                               for p in self.reachable(u))
            for target in self.units.values():
                if not is_alive(target) or target['team'] == u['team']:
                    continue
                for base_weapon in u['weapons']:
                    w=(self.rules.effective_weapon(u,target,base_weapon) if hasattr(self.rules,'effective_weapon') else base_weapon)
                    if (w['en'] <= u['en'] and self.within_range(u,target,w)
                            and (not hasattr(self.rules,'weapon_available')
                                 or self.rules.weapon_available(u,target,w))):
                        actions.append(dict(type='attack', unit=u['id'],
                                            target=target['id'], weapon=w['id']))
            actions.append(dict(type='wait', unit=u['id']))
        if hasattr(self, '_skill_actions'):actions.extend(self._skill_actions())
        actions.append(dict(type='end_turn'))
        return actions

    def step(self, action):
        # Check before touching state or consuming RNG: invalid actions are atomic.
        if action not in self.legal_actions():
            raise ValueError('Illegal action')
        event = dict(turn=self.turn, team=self.active_team, action=deepcopy(action))
        if action['type'] == 'skill':
            event.update(self._execute_skill(action))
        elif action['type'] == 'end_turn':
            if self.phase_clock is not None:
                from .turns import init_unit_phase
                event['phase_transitions']=[]
                for _ in range(6):
                    value=self.phase_clock.advance();self.turn=value.turn
                    record=dict(turn=value.turn,side=value.id)
                    event['phase_transitions'].append(record)
                    if hasattr(self, '_check_stage_outcome') and self._check_stage_outcome():
                        break
                    if self.turn>self.max_turns:
                        self.truncated=True;break
                    for u in self.units.values():
                        if u['team']==self.active_team:init_unit_phase(u)
                    if hasattr(self,'_after_turn_progress'):self._after_turn_progress(record)
                    if any(u['team']==self.active_team and is_alive(u) for u in self.units.values()):
                        if hasattr(self,'_after_phase_start'):
                            event['phase_start_effects']=self._after_phase_start()
                        break
                event['turn_after']=self.turn
            elif self.turn >= self.max_turns:
                self.truncated = True
            else:
                self.turn += 1
                self.team_index = 1 - self.team_index
                for u in self.units.values():
                    if u['team'] == self.active_team:
                        u['moved'] = u['acted'] = False
                if hasattr(self,'_after_phase_start'):
                    event['phase_start_effects']=self._after_phase_start()
        else:
            unit = self.units[action['unit']]
            if action['type'] == 'move':
                unit['position'] = list(action['to'])
                unit['moved'] = True
                if 'chance_step' in unit:unit['chance_step']['has_moved']=True
                if hasattr(self, 'skill_effect_runtime'):
                    event['consumed_skill_effects'] = self.skill_effect_runtime.consume(unit, {8})
            elif action['type'] == 'wait':
                unit['acted'] = True
                if 'chance_step' in unit:unit['chance_step']['existence']=1
            else:
                target = self.units[action['target']]
                weapon = next(w for w in unit['weapons'] if w['id'] == action['weapon'])
                if hasattr(self, '_execute_exchange'):
                    unit['acted'] = True
                    event.update(self._execute_exchange(unit, target, weapon))
                else:
                    if hasattr(self.rules,'effective_weapon'):weapon=self.rules.effective_weapon(unit,target,weapon)
                    if not hasattr(self.rules,'consume_weapon'):unit['en'] -= weapon['en']
                    unit['acted'] = True
                    if hasattr(self.rules,'resolve_attack'):
                        resolution = self.rules.resolve_attack(unit,target,weapon,self.rng)
                        hit, critical, damage = resolution['hit'], resolution['critical'], resolution['damage']
                        hit_roll, critical_roll = resolution.get('hit_roll'), resolution.get('critical_roll')
                        event.update(resolution)
                    else:
                        hit_roll = self.rng.randrange(100)
                        hit = hit_roll < max(0, min(100, weapon['hit']))
                        critical_roll = self.rng.randrange(100) if hit else None
                        critical = hit and critical_roll < max(0, min(100, weapon['critical']))
                        damage = self.rules.damage(unit, target, weapon, critical) if hit else 0
                    if hasattr(self.rules,'consume_weapon'):event.update(self.rules.consume_weapon(unit,weapon))
                    from .weapon_drain import apply_hp_drain,apply_en_drain
                    apply_hp_drain(unit,event)
                    apply_en_drain(unit,event)
                    from .hp_barrier import apply_barrier_damage
                    apply_barrier_damage(target,event)
                    from .unit_life import apply_hp_damage
                    applied=apply_hp_damage(target,event,damage)
                    from .weapon_resource_damage import apply_resource_damage
                    apply_resource_damage(target,event)
                    from .weapon_debuffs import grant_debuffs
                    grant_debuffs(self,unit,target,event)
                    if not is_alive(target) and 'chance_step' in target:target['chance_step']['existence']=2
                    event.update(hit=hit, critical=critical, hit_roll=hit_roll,
                                 critical_roll=critical_roll, damage=damage, applied_damage=applied)
                    if hasattr(self, 'skill_effect_runtime'):
                        event['consumed_skill_effects'] = self.skill_effect_runtime.consume(unit, {6, 7})
                    if hasattr(self.rules,'reflect_battle_mp'):
                        self.rules.reflect_battle_mp(unit)
                        self.rules.reflect_battle_mp(target)
                if hasattr(self,'_after_attack'):self._after_attack(unit,target,event)
                if (hasattr(self,'skill_effect_runtime')
                        and event.get('chance_step',{}).get('consumed_timing') == 15):
                    event.setdefault('consumed_skill_effects', []).extend(
                        self.skill_effect_runtime.consume(unit, {15}))
                alive = {u['team'] for u in self.units.values() if is_alive(u)}
                if len(alive) == 1 and not hasattr(self, '_check_stage_outcome'):
                    self.terminated, self.winner = True, alive.pop()
        if hasattr(self,'_refresh_state'):self._refresh_state()
        if hasattr(self, '_check_stage_outcome'):
            outcome = self._check_stage_outcome()
            if outcome is not None:event['stage_outcome'] = deepcopy(outcome)
        self.events.append(event)
        return dict(observation=self.observe(), event=deepcopy(event),
                    terminated=self.terminated, truncated=self.truncated)

    def replay(self):
        return deepcopy(dict(scenario=self.scenario, seed=self.seed,
                             rules=getattr(self.rules,'name','approximate-v1'), master_sha256=self.master.sha256,
                             critical_multiplier=getattr(self.rules,'critical_multiplier',None),
                             events=self.events, final=self.observe()))
