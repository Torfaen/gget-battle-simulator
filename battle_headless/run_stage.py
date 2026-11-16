"""Run scripted account battles and retain explicit acceptance blockers.

Scripts are JSON arrays of BattleEnv actions, in execution order for both teams.
--auto uses the existing test heuristic after the script, not original enemy AI.
Incomplete profiles stop before actions unless --allow-incomplete is explicit.
"""
import argparse
import hashlib
import json
from copy import deepcopy
from dataclasses import asdict,is_dataclass
from pathlib import Path
from battle_paths import OUTPUTS

from .account import DEFAULT_SNAPSHOT
from .original import SOURCE_LIB_SHA256
from .random_original import OriginalRandom
from .session import make_account_factory
from .stage import DEFAULT_STAGE


class RecordedRandom(OriginalRandom):
    """Record calls and raw draws without changing original short circuits."""
    def __init__(self, state):
        self.setstate(state)
        self.calls = []
        self.draws = []

    def uint32(self):
        value = super().uint32()
        self.draws.append(value)
        return value

    def range_int(self, minimum, maximum):
        record = dict(method='range_int', minimum=minimum, maximum=maximum,
                      draw_start=len(self.draws))
        self.calls.append(record)
        value = super().range_int(minimum, maximum)
        record.update(result=value, draw_end=len(self.draws))
        return value

    def evaluate(self, rate):
        record = dict(method='evaluate', rate=rate, draw_start=len(self.draws))
        self.calls.append(record)
        value = super().evaluate(rate)
        record.update(result=value, draw_end=len(self.draws))
        return value


def _recorded_state(value):
    """Retain runtime state plus callback provenance in JSON-safe snapshots."""
    if is_dataclass(value) and not isinstance(value,type):return _recorded_state(asdict(value))
    if callable(value):return dict(callback=value.__module__+'.'+value.__qualname__)
    if isinstance(value,dict):return {k:_recorded_state(v) for k,v in value.items()}
    if isinstance(value,list):return [_recorded_state(v) for v in value]
    if isinstance(value,tuple):return tuple(_recorded_state(v) for v in value)
    return deepcopy(value)


def state_snapshot(env):
    fields = ('id', 'unit_id', 'native_unit_id', 'native_unit_step_order', 'team', 'position', 'direction', 'standby_terrain_type',
              'unit_standby_type', 'existence', 'hp', 'en', 'mp', 'sp', 'moved', 'acted',
              'attack', 'defense', 'mobility', 'pilot', 'chance_step', 'support_budget',
              'scalar_corrections', 'active_weapon_debuffs', 'active_skill_effects',
              'runtime_trait_effects', 'map_weapon_used_counts')
    return _recorded_state(dict(turn=env.turn, team=env.active_team,
        phase_side=env.phase_clock.side if env.phase_clock else None,
        units=[{k:u[k] for k in fields if k in u} for u in env.units.values()],
        runtime_trait_groups=getattr(env, 'runtime_trait_sets', {}),
        stage_outcome=getattr(env, 'stage_outcome', None)))


def initialization_blockers(env):
    blockers = [dict(kind='implementation', system='initialization',
        reason='Initial construction inventory is separate; global activation and stage-added sources are not connected'),
        dict(kind='implementation', system='deployment',
            reason='Original player formation coordinates and sortie terrain connected; NPC deployment and complete global initialization remain unverified'),
        dict(kind='evidence', system='stage_events',
        reason='Stage bundle/scripts and full outcome caller timing are unverified')]
    for unit in env.units.values():
        for key in ('pending_passives', 'pending_character_skills', 'pending_supporter_effects'):
            for item in unit.get(key, []):
                blockers.append(dict(kind='implementation', system=key,
                    unit=unit['id'], detail=deepcopy(item)))
    return blockers


def event_blockers(value, path='event'):
    found = []
    if isinstance(value, dict):
        for key, child in value.items():
            location = path+'.'+str(key)
            if ('pending' in str(key) and child) or (key == 'action_advanced' and child is False):
                found.append(dict(kind='implementation', system=location, detail=deepcopy(child)))
            else:
                found.extend(event_blockers(child, location))
    elif isinstance(value, list):
        for i, child in enumerate(value):
            found.extend(event_blockers(child, path+f'[{i}]'))
    return found


def run_battle(env, actions=(), *, auto=False, allow_incomplete=False, max_actions=1000):
    if type(max_actions) is not int or max_actions < 1:
        raise ValueError('max_actions must be a positive integer')
    actions = list(actions)
    if any(not isinstance(a, dict) for a in actions):
        raise ValueError('Action script must contain action objects')
    from .__main__ import choose
    rng = RecordedRandom(env.rng.getstate())
    env.rng = rng
    blockers = initialization_blockers(env)
    if auto:
        blockers.append(dict(kind='test_policy', system='decision',
            reason='Automatic actions use a test heuristic, not the original decision service'))
    result = dict(schema='headless-stage-run-v1', exact_gameplay=False,
        baseline=deepcopy(getattr(env, 'battle_baseline', {})),
        deployment=deepcopy(getattr(env, 'deployment_profile', {})),
        source_lib_sha256=SOURCE_LIB_SHA256, initial=state_snapshot(env),
        input_actions=deepcopy(actions), auto=auto, allow_incomplete=allow_incomplete,
        blockers=blockers, steps=[])
    if hasattr(env,'initial_trait_inventory'):
        from .initial_trait_inventory import inventory_report
        result['initial_trait_construction']=inventory_report(env.initial_trait_inventory)
    stop = 'initialization_blocked'
    if not blockers or allow_incomplete:
        stop = 'script_exhausted'
        for index in range(max_actions):
            if env.terminated or env.truncated:
                break
            if index < len(actions):
                action = deepcopy(actions[index])
            elif auto:
                action = choose(env)
            else:
                break
            before = state_snapshot(env)
            draws, calls = len(rng.draws), len(rng.calls)
            try:
                step = env.step(action)
            except Exception as error:
                stop = 'execution_error'
                blockers.append(dict(kind='execution', system='action', index=index,
                    action=action, error_type=type(error).__name__, reason=str(error),
                    before=before, after=state_snapshot(env)))
                break
            event = step['event']
            result['steps'].append(dict(index=index, action=action, event=event,
                before=before, after=state_snapshot(env),
                rng_draw_range=[draws, len(rng.draws)], rng_call_range=[calls, len(rng.calls)]))
            pending = event_blockers(event)
            blockers.extend(pending)
            if pending and not allow_incomplete:
                stop = 'unsupported_rule'
                break
        else:
            stop = 'action_limit'
        if env.truncated:
            stop = 'truncated'
        elif env.terminated:
            stop = 'stage_outcome' if getattr(env, 'stage_outcome', None) else 'non_stage_termination'
    result.update(stop_reason=stop, terminated=env.terminated, truncated=env.truncated,
        winner=env.winner, stage_outcome=deepcopy(getattr(env, 'stage_outcome', None)),
        script_actions_consumed=min(len(actions), len(result['steps'])),
        completed=stop == 'stage_outcome' and not blockers,
        reached_stage_outcome=stop == 'stage_outcome',
        final=state_snapshot(env), rng_calls=rng.calls, rng_draws=rng.draws,
        rng_final_state=rng.getstate())
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument('--stage', type=int, default=DEFAULT_STAGE)
    parser.add_argument('--seed', type=int, default=37)
    parser.add_argument('--script', type=Path)
    parser.add_argument('--auto', action='store_true')
    parser.add_argument('--allow-incomplete', action='store_true')
    parser.add_argument('--max-actions', type=int, default=1000)
    parser.add_argument('--out', type=Path, default=OUTPUTS / 'battle_headless/hard_run.json')
    args = parser.parse_args()
    actions = json.loads(args.script.read_text(encoding='utf-8-sig')) if args.script else []
    if not isinstance(actions, list):
        parser.error('Script must be a JSON array of actions')
    env = make_account_factory(snapshot=args.snapshot, stage_id=args.stage)(args.seed)
    env.battle_baseline['snapshot_sha256'] = hashlib.sha256(args.snapshot.read_bytes()).hexdigest()
    result = run_battle(env, actions, auto=args.auto, allow_incomplete=args.allow_incomplete,
                        max_actions=args.max_actions)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k:result[k] for k in ('stop_reason', 'completed',
        'reached_stage_outcome', 'winner', 'exact_gameplay')}, ensure_ascii=False))
    return 0 if result['completed'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
