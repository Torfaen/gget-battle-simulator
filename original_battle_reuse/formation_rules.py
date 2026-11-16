"""Status, leader bonuses and terrain checks for the sortie formation window."""

from dataclasses import dataclass
from functools import cmp_to_key
import math
import struct


def i32(value):
    return (int(value) + 2**31) % 2**32 - 2**31


def f32(value):
    try:
        return struct.unpack('<f', struct.pack('<f', value))[0]
    except OverflowError:
        return math.copysign(math.inf, value)


def round_down(value):
    """Apply the client's single-precision truncation."""
    value = f32(value)
    if math.isnan(value):
        return 0
    if math.isinf(value):
        return -2**31
    return min(2**31 - 1, max(-2**31, int(value)))


def round_up_double(value):
    if math.isnan(value):
        return 0
    if math.isinf(value):
        return -2**31
    rounded = math.floor(value) if value < 0 else math.ceil(value)
    return max(-2**31, min(2**31 - 1, rounded))


def truncdiv(value, divisor):
    magnitude = abs(value) // abs(divisor)
    return -magnitude if (value < 0) != (divisor < 0) else magnitude


def character_stat(base, rate=0, flat=0):
    """UserCharacterStatus._CalcStatus, with growth already applied."""
    correction = round_down(f32(i32(base * rate)) / 100)
    return min(99999, max(0, i32(base + flat + correction)))


def _bits(mask, start=0):
    return {start + bit for bit in range(mask.bit_length()) if mask & (1 << bit)}


# Pilot power uses A/B for the total and O–X for the five pilot stats.
PILOT_RATING_TYPES = {
    'A': {13, 20, 84, 93, 96, 103, 104, 105, 106, 110, 111} | _bits(0x2a3c011, 54),
    'B': _bits(0x80006) | _bits(0x80000003, 49) | _bits(0x19, 85) | _bits(0x1181, 107),
    'O': {22}, 'P': {21}, 'Q': {24}, 'R': {23}, 'S': {26},
    'T': {25}, 'U': {28}, 'V': {27}, 'W': {30}, 'X': {29},
}


@dataclass(frozen=True)
class RatingTrait:
    kind: int
    rating: int

    def point(self, component):
        return i32(self.rating) if i32(self.kind) in PILOT_RATING_TYPES[component] else 0


def aggregate(traits, component):
    return i32(sum(trait.point(component) for trait in traits)) / 100.0


def stat_point(base, flat, rate):
    return flat + ((rate + 1.0) * float(i32(base)))


def additional_parameter(maximum, growth_basis_points):
    # UserSupporterEntity rounds at each single-precision operation.
    rate = f32(f32(growth_basis_points) / 10000)
    value = f32(f32(maximum) * rate)
    if not math.isfinite(value):
        return round_down(value)
    return round_down(math.floor(value) if value < 0 else math.ceil(value))


def leader_uses_or(groups, predicate_count):
    groups = set(groups)
    return len(groups) == 1 and bool(next(iter(groups))) and predicate_count > 1


def leader_matches(rows, tags, series, role):
    """Evaluate native leader predicates against one unit's master data."""
    use_or = leader_uses_or((group for group, *_ in rows),
                            sum(len(field) for row in rows for field in row[1:]))
    if use_or:
        return any(bool(unit_tags & tags or unit_series & series) or role in roles
                   for _, unit_tags, roles, unit_series in rows)
    return all(unit_tags <= tags and unit_series <= series and (not roles or role in roles)
               for _, unit_tags, roles, unit_series in rows)


TERRAIN_PRIORITY = {2: 1, 3: 3, 4: 2, 5: 4, 6: 5}
TERRAIN_CHANGE_TYPES = {104: (1, 2), 105: (2, 3), 106: (1, 3)}
STAGE_TERRAIN_TYPES = {0: (0,), 1: (2,), 2: (3,), 3: (4, 3), 4: (3, 5, 6), 5: (4, 3, 5, 6)}


def change_record(trait):
    source, target = TERRAIN_CHANGE_TYPES.get(trait['TraitTypeIndex'], (1, 1))
    return dict(target_terrain_type=i32(trait['TraitValue']), from_capability=source,
                to_capability=target, source_trait_id=trait['Id'])


def compare_preference(first, second):
    difference = i32(second[1] - first[1])
    return difference if difference else i32(TERRAIN_PRIORITY[first[0]] - TERRAIN_PRIORITY[second[0]])


def upgrade(base_values, option_parts_changes):
    """Apply each equipped part's terrain change against the original capability."""
    base = {kind: i32(base_values[kind] if kind in base_values else base_values[str(kind)])
            for kind in TERRAIN_PRIORITY}
    result = dict(base)
    for change in option_parts_changes or ():
        kind = i32(change['target_terrain_type'])
        if kind in base and base[kind] == i32(change['from_capability']):
            result[kind] = i32(change['to_capability'])
    rows = [(kind, value) for kind, value in result.items() if value != 1]
    if rows and max(value for _, value in rows) - min(value for _, value in rows) > 2**31 - 1:
        raise NotImplementedError('Original overflowing capability-pair List sort required')
    rows.sort(key=cmp_to_key(compare_preference))
    return dict(capabilities=result, sorted_standby_terrains=[kind for kind, _ in rows])


def sortie_status(capability, stage_type):
    """Return the original 0/1 permitted or 2 refused sortie status."""
    stage_type = i32(stage_type)
    if stage_type not in STAGE_TERRAIN_TYPES:
        raise ValueError('Unknown original stage terrain type')
    values = capability['capabilities']
    if stage_type == 5:
        atmosphere = i32(values[3])
        if atmosphere > 2:
            return 0
        ground, surface, underwater = (i32(values[kind]) for kind in (4, 5, 6))
        if atmosphere == 1 and (ground == 1 or surface == underwater == 1):
            return 2
        return 0 if 3 in (ground, surface, underwater) else 1
    result = 2
    for kind in STAGE_TERRAIN_TYPES[stage_type]:
        current = i32(values[kind]) if kind in TERRAIN_PRIORITY else 1
        if current == 3:
            return 0
        if current == 2:
            result = 1
    return result
