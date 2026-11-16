"""Dependency-free RL API; optional Gymnasium adapter lives in gym_env.py."""
from copy import deepcopy
from collections import Counter
import argparse
import json
import random
import time

from .__main__ import choose
from .mechanics import SupportBudget
from .skill_effects import skill_buff_observation


def make_factory(profile='account-hard'):
    """Load master/account data once, rebuild mutable battle state per episode."""
    if profile == 'demo':
        from pathlib import Path
        from .engine import BattleEnv, MasterData
        master = MasterData()
        scenario = json.loads(Path(__file__).with_name('demo.json').read_text())
        def factory(seed):
            env = BattleEnv(master)
            env.reset(scenario, seed)
            return env
        return factory
    if profile == 'hard':
        from .stage import StageFixture
        from .stage_smoke import build_env
        stage = StageFixture()
        return lambda seed: build_env(stage, seed)[0]
    if profile != 'account-hard':
        raise ValueError(profile)
    from .session import make_account_factory
    return make_account_factory()


class BattleRL:
    """Single player agent versus deterministic heuristic enemy.

    Fixed action IDs enumerate end turn, wait, every board destination and
    every weapon/target pair. Mask invalid actions before sampling. Invalid
    actions raise without advancing the battle. Reward is change in average
    team HP fractions (+enemy damage, -friendly damage), -0.001 per decision,
    plus +1/-1 for victory/defeat. Truncations have no terminal bonus.
    """
    def __init__(self, factory, *, player_team='player', max_steps=1000,
                 max_auto_steps=5000):
        if max_steps < 1 or max_auto_steps < 1:
            raise ValueError('Step limits must be positive')
        self.factory = factory
        self.player_team = player_team
        self.max_steps = max_steps
        self.max_auto_steps = max_auto_steps
        self.env = factory(0)
        self.unit_ids = tuple(self.env.units)
        self.shape = (self.env.width, self.env.height)
        if player_team not in {u['team'] for u in self.env.units.values()}:
            raise ValueError('Player team is absent')
        self.actions = [{'type': 'end_turn'}]
        for uid, unit in self.env.units.items():
            if unit['team'] != player_team:
                continue
            self.actions.append(dict(type='wait', unit=uid))
            self.actions.extend(dict(type='move', unit=uid, to=[x, y])
                                for y in range(self.env.height)
                                for x in range(self.env.width))
            self.actions.extend(dict(type='attack', unit=uid, target=tid, weapon=w['id'])
                                for w in unit['weapons']
                                for tid, target in self.env.units.items()
                                if target['team'] != player_team)
        self.actions.extend(deepcopy(getattr(self.env, 'skill_action_catalog', [])))
        self.lookup = {self._key(a): i for i, a in enumerate(self.actions)}
        if len(self.lookup) != len(self.actions):
            raise ValueError('Duplicate action IDs')
        self.reset(seed=0)

    @staticmethod
    def _key(action):
        return json.dumps(action, sort_keys=True, separators=(',', ':'))

    def reset(self, *, seed=None, options=None):
        if seed is None:
            seed = random.randrange(2**32)
        self.env = self.factory(seed)
        if tuple(self.env.units) != self.unit_ids or (self.env.width, self.env.height) != self.shape:
            raise ValueError('Factory changed scenario layout or unit IDs')
        self.steps = 0
        self.limit_reached = False
        self._initial_hp = {uid: max(1, u['max_hp']) for uid, u in self.env.units.items()}
        self._auto()
        return self._observation(), self._info()

    def _done(self):
        return self.env.terminated or self.env.truncated or self.limit_reached

    def _auto(self):
        count = 0
        while not self._done() and self.env.active_team != self.player_team:
            if count >= self.max_auto_steps:
                self.limit_reached = True
                break
            self.env.step(choose(self.env))
            count += 1

    def action_masks(self):
        mask = [False] * len(self.actions)
        if not self._done():
            for action in self.env.legal_actions():
                mask[self.lookup[self._key(action)]] = True
        return mask

    def _score(self):
        groups = {True: [], False: []}
        for uid, u in self.env.units.items():
            groups[u['team'] == self.player_team].append(max(0, u['hp']) / self._initial_hp[uid])
        return sum(groups[True]) / len(groups[True]) - sum(groups[False]) / len(groups[False])

    def _observation(self):
        # Stable unit order, dead units retained. Features are numeric, finite.
        w, h = self.shape
        units = []
        for uid in self.unit_ids:
            u = self.env.units[uid]
            budget = SupportBudget(**u.get('support_budget', {}))
            units.append([float(u['team'] == self.player_team), float(u['hp'] > 0),
                          max(0, u['hp']) / max(1, u['max_hp']),
                          max(0, u['en']) / max(1, u['max_en']),
                          u['position'][0] / max(1, w-1), u['position'][1] / max(1, h-1),
                          float(u['moved']), float(u['acted']),
                          u['attack'] / 10000, u['defense'] / 10000,
                          u['mobility'] / 10000, u['movement'] / max(w, h),
                          u.get('sp', 0) / max(1, u.get('max_sp', 1)), u.get('mp', 0) / 12,
                          max(0, budget.maximum()-budget.current()) / max(1, budget.maximum()),
                          budget.support_type() / 2])
        chance = []
        for uid in self.unit_ids:
            u = self.env.units[uid];state = u.get('chance_step', {});c = u.get('scalar_corrections', {})
            chance.append([state.get('used',0)/10,
                max(0,1+c.get('ChanceStepMaxCount',0))/10 if state.get('body_type')==1 else 0,
                state.get('once',0)/10, state.get('guaranteed_once',0)/10,
                float(state.get('guaranteed',False) or c.get('GuaranteedChanceStepCount',0)>0),
                float(state.get('weapon_used_guaranteed',False) or c.get('GuaranteedChanceStepWeaponUsedOnlyCount',0)>0)])
        return dict(units=units, chance_steps=chance,
                    moment_trigger=[int(bool(self.env.moment_trigger.get(key,False)))
                                    for key in ('exists','is_success','is_failure')],
                    skill_buffs=[skill_buff_observation(self.env.units[uid]) for uid in self.unit_ids],
                    clock=[self.env.turn / max(1, self.env.max_turns),
                    self.steps / self.max_steps], action_mask=self.action_masks())

    def _info(self):
        return dict(winner=self.env.winner, turn=self.env.turn, steps=self.steps,
                    limit_reached=self.limit_reached, exact_gameplay=False,
                    stage_outcome=deepcopy(getattr(self.env, 'stage_outcome', None)),
                    counter_policy_profile=getattr(self.env, 'counter_policy_profile', 'counter-only-subset'))

    def step(self, action):
        if self._done():
            raise RuntimeError('Episode finished; call reset')
        if not isinstance(action, int) or not 0 <= action < len(self.actions) or not self.action_masks()[action]:
            raise ValueError('Invalid or masked action')
        before = self._score()
        self.env.step(deepcopy(self.actions[action]))
        self.steps += 1
        self._auto()
        if self.steps >= self.max_steps and not self.env.terminated:
            self.limit_reached = True
        reward = self._score() - before - 0.001
        if self.env.terminated:
            reward += 1 if self.env.winner == self.player_team else -1
        return (self._observation(), reward, self.env.terminated,
                (self.env.truncated or self.limit_reached) and not self.env.terminated, self._info())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', choices=['demo', 'hard', 'account-hard'], default='account-hard')
    parser.add_argument('--episodes', type=int, default=10)
    parser.add_argument('--seed', type=int, default=37)
    args = parser.parse_args()
    env = BattleRL(make_factory(args.profile))
    rng = random.Random(args.seed)
    start = time.perf_counter()
    results = []
    for episode in range(args.episodes):
        obs, info = env.reset(seed=args.seed + episode)
        total = 0
        while not env._done():
            action = rng.choice([i for i, valid in enumerate(obs['action_mask']) if valid])
            obs, reward, terminated, truncated, info = env.step(action)
            total += reward
        results.append(dict(**info, reward=total,
                            counters=sum(bool(e.get('counter')) for e in env.env.events),
                            skills=sum(e['action']['type']=='skill' for e in env.env.events),
                            timed_skill_uses=sum(e['action']['type']=='skill' and
                                any(effect.get('action_timing') == 3 for effect in e.get('effects', []))
                                for e in env.env.events),
                            supports=sum(len(e.get('support_attacks', []))+len(e.get('support_counters', []))
                                         for e in env.env.events),
                            support_defenses=sum(bool(e.get('support_defense')) for e in env.env.events),
                            counter_responses=dict(Counter(e['counter_selection'].get('action_type', 'Counter')
                                for e in env.env.events if 'counter_selection' in e))))
    seconds = time.perf_counter() - start
    print(json.dumps(dict(profile=args.profile, episodes=results, seconds=seconds,
                          decisions_per_second=sum(r['steps'] for r in results)/max(seconds, 1e-9)), ensure_ascii=False))


if __name__ == '__main__':
    main()
