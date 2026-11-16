"""Original rating projections and double-precision battle-power arithmetic.

Inputs are outgame unit/character base stats and rating weapons/traits. They
must not be populated from damaged or currently buffed combat entities.
"""
import math
from dataclasses import dataclass
from .original import i32


def _bits(mask,start=0):return {start+n for n in range(mask.bit_length()) if mask&(1<<n)}


RATING_TYPES={
    'A':{13,20,84,93,96,103,104,105,106,110,111}|_bits(0x2a3c011,54),
    'B':_bits(0x80006)|_bits(0x80000003,49)|_bits(0x19,85)|_bits(0x1181,107),
    'C':{4,32},'D':{3,31}|_bits(0x4004001,83),
    'E':_bits(0x2400000040),'F':{5,33,36,98,101},
    'G':_bits(0x10000040100)|{43,48,73,116},
    'H':{7,17,38,39,86,94,99,108}|_bits(0x8c40000000000)|_bits(0x4005,62),
    'I':{10,15},'J':_bits(0x14200)|_bits(0x4800000001,52),'K':{12},'L':{11},
    'M':{35,72,118},'N':{95,100}|_bits(0xc03,81)|_bits(0x400000003,44)|_bits(0x8c01,102),
    'O':{22},'P':{21},'Q':{24},'R':{23},'S':{26},'T':{25},'U':{28},'V':{27},'W':{30},'X':{29},
    'CNonPercent':set(),'GNonPercent':set(),
}


@dataclass(frozen=True)
class RatingTrait:
    kind: int
    rating: int
    boost_count: int=1

    def point(self,component):
        if i32(self.kind) not in RATING_TYPES[component]:return 0
        return i32(i32(self.rating)*i32(self.boost_count)) if component in 'CDEFGHIJKL' else i32(self.rating)


def aggregate(traits,component,nonpercent=None):
    percent=i32(sum(t.point(component) for t in traits))
    flat=i32(sum(t.point(nonpercent) for t in traits)) if nonpercent else 0
    return float(percent)/100.0+float(flat)


def round_up_double(value):
    if math.isnan(value):return 0
    if math.isinf(value):return -2**31
    rounded=math.floor(value) if value<0 else math.ceil(value)
    return max(-2**31,min(2**31-1,rounded))


def stat_point(base,flat,rate,divisor=1):
    return (flat+((rate+1.0)*float(i32(base))))/divisor


def weapon_rating(base_rating,level_coefficient,core_rating=0):
    return ((float(i32(level_coefficient))/100.0)*float(i32(i32(base_rating)+i32(core_rating))))/100.0


def character_weapon_point(character,attack,weapons,flat=0.0,rate=0.0):
    """0x6130ec8: strict comparison, attribute order 1/2/3, ID exclusion."""
    best=0.0;selected_id=0;selected_character=0.0;selected_rating=0.0
    for weapon in weapons:
        rating=weapon['rating']
        for attribute,field in ((1,'ranged'),(2,'melee'),(3,'awaken')):
            if attribute not in weapon['attributes']:continue
            candidate=(character[field]+attack)*rating
            if candidate>best:
                best=candidate;selected_id=weapon['id'];selected_character=character[field];selected_rating=rating
    others=0.0
    for weapon in weapons:
        if weapon['id']!=selected_id:others+=weapon['level_up_rating']
    return selected_character,flat+((rate+1.0)*(selected_rating+others/10.0))


def calculate_battle_power(unit,character,weapons,traits,coefficient):
    """Nonblank unit+character path used by all twelve fixed-stage NPCs."""
    unit_points={}
    for field,a,b,non,divisor in [('hp','C','D','CNonPercent',100.0),('en','E','F',None,1.0),
        ('attack','G','H','GNonPercent',10.0),('defense','I','J',None,10.0),('mobility','K','L',None,10.0)]:
        unit_points[field]=stat_point(unit[field],aggregate(traits,a,non),aggregate(traits,b),divisor)
    character_points={field:stat_point(character[field],aggregate(traits,a),aggregate(traits,b))
        for field,a,b in [('ranged','O','P'),('melee','Q','R'),('awaken','S','T'),('defense','U','V'),('reaction','W','X')]}
    chosen,rating=character_weapon_point(character_points,unit_points['attack'],weapons,aggregate(traits,'M'),aggregate(traits,'N'))
    combined=chosen+((character_points['defense']+character_points['reaction'])*0.5)
    total=unit_points['hp']+unit_points['en']
    total+=unit_points['attack'];total+=unit_points['defense'];total+=unit_points['mobility'];total+=combined
    total+=rating*(unit_points['attack']+chosen)
    total=(aggregate(traits,'B')+1.0)*total
    total=aggregate(traits,'A')+total
    raw=(total*float(i32(coefficient)))/100.0
    return dict(power=round_up_double(raw),raw=raw,unit_points=unit_points,character_points=character_points,
        selected_character_point=chosen,weapon_point=rating)
