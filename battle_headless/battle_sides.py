"""Original eight-side relations; friendly and allied are distinct."""
from .original import i32

OPPOSITION_MASKS=(0x6c,0x6c,0x5b,0x37,0x6c,0x5b,0x37,0)
ALLY_MASKS=(0x83,0x83,0xa4,0xc8,0x90,0xa4,0xc8,0xff)


def _source(side):
    side=i32(side)
    if not 0<=side<=7:raise ValueError('Native side relation requires a source in 0..7')
    return side


def is_opposition(side,target):
    side=_source(side);target=i32(target)
    return 0<=target<=7 and bool(OPPOSITION_MASKS[side]&(1<<target))


def is_ally(side,target):
    side=_source(side);target=i32(target)
    return side==7 or (0<=target<=7 and bool(ALLY_MASKS[side]&(1<<target)))


def is_ally_or_friendly(side,target):
    return not is_opposition(side,target)


def is_alive_on_map(existence):
    return (i32(existence)&0xffffffff)<2
