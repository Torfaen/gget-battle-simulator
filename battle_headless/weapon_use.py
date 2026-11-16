"""Weapon resource mutation recovered from IngameUnitEntity.UseWeapon.

Battle calculation precedes this call. HP/EN change now; MP is accumulated and
only committed by ReflectMpChangeValue at the battle MP reflection boundary.
"""
from .original import i32


def use_weapon(unit,en_cost,mp_cost,hp_cost,*,predicting=False):
    """UseWeapon 0x613b6a0; costs must be evaluated by original cost services."""
    return use_weapon_with_getters(unit,lambda:en_cost,lambda:mp_cost,lambda maximum:hp_cost,predicting=predicting)


def use_weapon_with_getters(unit,en_cost_getter,mp_cost_getter,hp_cost_getter,*,predicting=False):
    """Evaluate each bound weapon getter at its original mutation boundary."""
    if predicting:return dict(resource_use_skipped=True)
    from .weapon_resource_damage import plus_en
    from .unit_damage import plus_hp,add_mp_change
    before=dict(hp=unit['hp'],en=unit['en'],mp=unit['mp'])
    en_cost=en_cost_getter();plus_en(unit,i32(-en_cost))
    mp_cost=mp_cost_getter()
    if mp_cost is not None:add_mp_change(unit,i32(-mp_cost))
    hp_cost=hp_cost_getter(unit['max_hp'])
    if hp_cost is not None:
        # _PlusHp uses Math.Min, unlike _PlusEn's two-sided clamp. Availability
        # prevents lethal costs in normal use; the raw mutator can go negative.
        plus_hp(unit,i32(-hp_cost))
    return dict(en_cost=en_cost,mp_cost=mp_cost,hp_cost=hp_cost,resource_before=before,
        resource_after=dict(hp=unit['hp'],en=unit['en'],mp=unit['mp']),
        pending_mp_change=unit.get('pending_mp_change',0))


def reflect_mp_change(unit):
    """ReflectMpChangeValue 0x6142300 / SetMp 0x613c03c, numeric state subset."""
    delta=unit.get('pending_mp_change',0)
    unit['mp_change_since_battle_start']=i32(unit.get('mp_change_since_battle_start',0)+delta)
    unit['mp']=max(0,min(12,i32(unit['mp']+delta)))
    unit['pending_mp_change']=0
