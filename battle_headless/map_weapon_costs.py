"""Original UnitMapWeapon costs from explicit level values and corrections."""
from .original import i32
from .weapon_corrections import en_consumption,percentage_delta,hp_consumption_rate,hp_consumption


def costs(unit,weapon,records):
    base=weapon['map_level_stats']
    mp_base=i32(base['mp_consumption'])
    # MAP differs from UnitWeapon: the MP upper bound is 9999, not 12.
    mp_total=min(9999,i32(mp_base+percentage_delta(records,'MpRate','MpValue',mp_base,weapon)))
    mp=None if mp_base==0 and mp_total<1 else max(0,mp_total)
    hp_rate=hp_consumption_rate(base['hp_consumption_rate'],records,weapon)
    return dict(en_cost=en_consumption(base['en'],records,weapon),mp_cost=mp,
                hp_cost=hp_consumption(unit['max_hp'],hp_rate),hp_consumption_rate=hp_rate)
