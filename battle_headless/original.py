"""Statically recovered scalar calculations from Android client 2.7.0.

Inputs are EFFECTIVE values after trait/pilot/terrain evaluation. This module
does not implement that evaluation or claim complete battle-system fidelity.
RVA provenance and differential validation are in docs/BATTLE_RESTORATION.md.
"""
import ctypes
import ctypes.util
import math
import os
import struct
from .engine import MasterData


def f32(value):
    try:
        return struct.unpack('<f', struct.pack('<f', value))[0]
    except OverflowError:
        return math.copysign(math.inf, value)


def add(a, b):
    return f32(f32(a)+f32(b))


def sub(a, b):
    return f32(f32(a)-f32(b))


def mul(a, b):
    return f32(f32(a)*f32(b))


def div(a, b):
    return f32(f32(a)/f32(b))


def i32(value):
    return (int(value)+2**31) % 2**32-2**31


_libm = ctypes.CDLL('ucrtbase' if os.name == 'nt' else ctypes.util.find_library('m'))
_expf = _libm.expf
_expf.argtypes, _expf.restype = [ctypes.c_float], ctypes.c_float


def rounding_exp(value):
    return max(float(_expf(f32(value))), f32(0.00001))


def round_down(value):
    """Client RoundDown is truncation toward zero, NOT mathematical floor."""
    value = f32(value)
    if math.isnan(value):
        return 0
    if value == math.inf:
        return -2**31
    if value == -math.inf:
        return -2**31
    return min(2**31-1, max(-2**31, int(value)))


def round_up(value):
    """Client RoundUp rounds away from zero, including negative values."""
    value = f32(value)
    if not math.isfinite(value):
        return round_down(value)
    return round_down(math.ceil(value) if value >= 0 else math.floor(value))


# Verified compressed signed enum defaults in version 2.7.0 metadata.
COEFFICIENT_IDS = {
    'hit_setting1': 14, 'hit_setting2': 15, 'hit_mobility_base': 16,
    'hit_mobility_gain': 17, 'hit_character_base': 18, 'hit_character_gain': 19,
    'unit_setting': 20, 'unit_gain': 21, 'character_setting': 22,
    'character_gain': 23, 'attack_base': 24, 'attack_limit': 25,
    'attack_gain': 26, 'defense_base': 27, 'defense_limit': 28, 'defense_gain': 29,
}
SOURCE_LIB_SHA256 = '1c969de7afd9903cfde5deed29a2050987c6e7dbb4dc2b257c1614beac27b443'
SOURCE_MASTER_SHA256 = '0efb0fe8c74da6461645e05ae4ae7aee33f452ce4b8accc005bc81c32146500f'


class OriginalScalarKernel:
    """Recovered formula layer; NOT a full rules implementation for BattleEnv."""
    def __init__(self, master=None):
        self.master = master or MasterData()
        if self.master.sha256 != SOURCE_MASTER_SHA256:
            raise ValueError('Scalar rules are pinned to the analyzed 2.7.0 master snapshot')
        values = {r['IngameCoefficientConfigTypeIndex']: r['Value']
                  for r in self.master.tables['m_ingame_coefficient_config']}
        self.config = {name: values[idx] for name, idx in COEFFICIENT_IDS.items()}

    def character_correction(self, attack, defense):
        return div(max(0, i32(attack-defense)), self.config['character_setting'])

    def unit_correction(self, attack, defense):
        return div(max(0, round_up(sub(div(attack, 10), div(defense, 10)))),
                   self.config['unit_setting'])

    def adjusted(self, attack, defense, kind):
        exponent = div(i32(i32(defense-attack)*self.config[kind+'_gain']), 100000)
        return div(1, add(rounding_exp(exponent), 1))

    def base_damage(self, power, attack, defense, character_attack, character_defense):
        cc = self.character_correction(character_attack, character_defense)
        ca = self.adjusted(character_attack, character_defense, 'character')
        uc = self.unit_correction(attack, defense)
        ua = self.adjusted(attack, defense, 'unit')
        # Preserve operation order emitted by the ARM64 compiler.
        return round_up(mul(add(uc, add(add(cc, ca), ua)), power))

    def damage_correction(self, base, attack, defense, character_attack, character_defense):
        c = self.config
        av = round_up(div(i32(attack+i32(character_attack << 1)), 10))
        dv = round_up(div(i32(defense+i32(character_defense << 1)), 10))
        attack_exp = rounding_exp(div(i32(i32(c['attack_base']-av)*c['attack_gain']), 100000))
        defense_exp = rounding_exp(div(i32(i32(c['defense_base']-dv)*c['defense_gain']), 100000))
        ac = div(div(c['attack_limit'], 100), add(attack_exp, 1))
        dc = div(div(c['defense_limit'], 100), add(defense_exp, 1))
        return mul(add(ac, dc), base)

    def battle_damage(self, power, attack, defense, character_attack, character_defense,
                      weapon_correction=0, weapon_efficiency=1.0):
        base = self.base_damage(power, attack, defense, character_attack, character_defense)
        correction = self.damage_correction(base, attack, defense, character_attack, character_defense)
        # CalcWeaponEffect and CalcMapEffect return zero in this client class.
        return round_up(mul(add(correction, i32(base+weapon_correction)), weapon_efficiency))

    def mobility_correction(self, attack_mobility, character_attack, target_mobility, reaction):
        c = self.config
        me = rounding_exp(div(i32(c['hit_mobility_gain']*i32(c['hit_mobility_base']-attack_mobility)), 10000))
        ce = rounding_exp(div(i32(c['hit_character_gain']*i32(c['hit_character_base']-character_attack)), 10000))
        mdiff = round_up(sub(div(attack_mobility, 10), div(target_mobility, 10)))
        unit = div(mul(add(me, 1), mdiff), c['hit_setting1'])
        pilot = div(mul(add(ce, 1), i32(character_attack-reaction)), c['hit_setting2'])
        return round_down(mul(add(pilot, unit), 100))

    @staticmethod
    def tension(mp, overflow=False):
        if overflow:
            return 5
        return 2 if mp < 4 else 3 if mp < 8 else 4 if mp <= 11 else 5

    @staticmethod
    def critical_type(tension):
        return 2 if tension == 5 else 1

    @staticmethod
    def defense_multiplier(counter_type, trait_reduction=0, shield_defense=0):
        return div(max(0, i32(80-i32(trait_reduction+shield_defense))), 100) if counter_type == 3 else f32(1)

    @staticmethod
    def final_damage(battle_damage, *, given_flat=0, critical_flat=0, taken_flat=0,
                     given_rate=0, weapon_rate=0, tension_rate=0, critical_rate=0,
                     taken_rate=0, defense_multiplier=1.0, critical_bonus=0,
                     hit_count=1, damage_cut=False, negate_threshold=0, upper_limit=0):
        rate = i32(given_rate+weapon_rate+tension_rate+critical_rate-taken_rate)
        rate_damage = round_up(div(i32(rate*battle_damage), 100))
        total = i32(battle_damage+given_flat+critical_flat-taken_flat+rate_damage)
        total = round_up(div(mul(mul(total, defense_multiplier), i32(critical_bonus+100)), 100))
        if damage_cut:
            return 0
        total = i32(max(0, total)*hit_count)
        if total <= negate_threshold:
            return 0
        return min(total, upper_limit) if upper_limit > 0 else total


def main():
    import argparse
    import json
    from pathlib import Path
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input', type=Path, required=True)
    p.add_argument('--out', type=Path)
    args = p.parse_args()
    case = json.loads(args.input.read_text(encoding='utf-8-sig'))
    kernel = OriginalScalarKernel()
    damage = case['damage']
    base_args = {k: damage[k] for k in ['power', 'attack', 'defense', 'character_attack', 'character_defense']}
    base = kernel.base_damage(**base_args)
    battle = kernel.battle_damage(**damage)
    result = dict(scope='recovered scalar calculations; effective inputs supplied by caller',
                  full_battle_system=False, version='2.7.0',
                  source_lib_sha256=SOURCE_LIB_SHA256, master_sha256=kernel.master.sha256,
                  base_damage=base, battle_damage=battle)
    if 'final' in case:
        result['final_damage_before_hp_barrier'] = kernel.final_damage(battle, **case['final'])
    if 'mobility' in case:
        result['mobility_hit_correction'] = kernel.mobility_correction(**case['mobility'])
    if 'mp' in case:
        result['tension'] = kernel.tension(case['mp'], case.get('overflow', False))
    output = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(output, encoding='utf-8')
    print(output)


if __name__ == '__main__':
    main()
