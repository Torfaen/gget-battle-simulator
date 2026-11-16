from .unit_life import is_alive
import argparse
import json
from pathlib import Path
from .engine import BattleEnv, MasterData, DEFAULT_MASTER


def choose(env):
    actions = env.legal_actions()
    attacks = [a for a in actions if a['type'] == 'attack']
    if attacks:
        return attacks[0]
    moves = [a for a in actions if a['type'] == 'move']
    enemies = [u for u in env.units.values() if u['team'] != env.active_team and is_alive(u)]
    def distance(pos):
        return min(sum(abs(a-b) for a, b in zip(pos, e['position'])) for e in enemies)
    moves = [a for a in moves if distance(a['to']) < distance(env.units[a['unit']]['position'])]
    return min(moves, key=lambda a: distance(a['to'])) if moves else {'type': 'end_turn'}


def main():
    parser = argparse.ArgumentParser(description='Approximate offline battle sandbox')
    parser.add_argument('--master', type=Path, default=DEFAULT_MASTER)
    parser.add_argument('--scenario', type=Path, default=Path(__file__).with_name('demo.json'))
    parser.add_argument('--seed', type=int, default=0)
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    if not args.master.is_file():
        parser.error(f'master snapshot missing: {args.master}. Supply --master or see README.md for the synthetic demo.')
    env = BattleEnv(MasterData(args.master))
    env.reset(json.loads(args.scenario.read_text(encoding='utf-8-sig')), args.seed)
    while not (env.terminated or env.truncated):
        env.step(choose(env))
    result = env.replay()
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(dict(rules=result['rules'], winner=env.winner,
                          turns=env.turn, actions=len(env.events),
                          terminated=env.terminated, truncated=env.truncated,
                          master_sha256=env.master.sha256), ensure_ascii=False))


if __name__ == '__main__':
    main()
