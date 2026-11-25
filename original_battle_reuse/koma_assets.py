"""Choose the chess-piece frames needed by the battle window."""

import re


DIRECTIONS = ('front', 'back', 'left', 'right')
FRONT = ('front',)
SIDE_IMAGE = re.compile(r'(?:original_battle_reuse/artifacts/)?idle_koma/([A-Za-z0-9_]+)/(?:back|left|right)\.png')


def directional_koma_ids(units, mounted_ids):
    """Collect resources for multi-cell units, mounted units, carriers and their forms."""
    rows = list(units)
    families = {family for identifier, _, _, family in rows if family and identifier in mounted_ids}
    return {resource for identifier, resource, area, family in rows
            if resource and (area > 1 or identifier in mounted_ids or family in families)}


def is_unused_koma_direction(name, directional):
    match = SIDE_IMAGE.fullmatch(name)
    return match is not None and match[1] not in directional
