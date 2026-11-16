"""Native MAP ammunition/configuration primitives; MAP execution is separate."""
from .original import i32
from .weapon_corrections import sum_field,matches_weapon


def ammo_capacity(level_capacity,records,weapon):
    return max(0,min(9999,i32(level_capacity+sum_field(records,'MaxAmmoValue',weapon))))


def ammo_unlimited(records,weapon):
    # Native tests each applicable correction; opposite-signed entries do not
    # cancel a positive source as they would in a sum-before-test shortcut.
    return any(matches_weapon(row.get('condition'),weapon) and
               i32(row.get('AmmoUnlimitedCount',0))>0 for row in records)


def has_remaining_ammo(level_capacity,used_count,records,weapon):
    return ammo_capacity(level_capacity,records,weapon)>i32(used_count) or ammo_unlimited(records,weapon)


def validate_suicide_configuration(weapon):
    if any(row['WeaponTraitTypeIndex']==4 for row in weapon.get('weapon_traits',[])):
        if weapon['map_range_type']!=1 or weapon['map_trait_category']!=1:
            raise ValueError('Native suicide MAP weapon requires range type 1 and trait category 1')


def used_ammo(unit,weapon):
    return i32(unit.get('map_weapon_used_counts',{}).get(str(weapon['main_weapon_id']),0))


def remaining_ammo(unit,weapon,level_capacity,records):
    # Native display remaining count does not substitute infinity for a
    # currently unlimited weapon. Availability is a separate predicate.
    return max(0,i32(ammo_capacity(level_capacity,records,weapon)-used_ammo(unit,weapon)))


def unit_has_remaining_ammo(unit,weapon,level_capacity,records):
    return has_remaining_ammo(level_capacity,used_ammo(unit,weapon),records,weapon)


def consume_map_ammo(unit,weapon,records):
    before=used_ammo(unit,weapon);unlimited=ammo_unlimited(records,weapon)
    if not unlimited:
        counts=unit.setdefault('map_weapon_used_counts',{})
        counts[str(weapon['main_weapon_id'])]=i32(before+1)
    return dict(main_weapon_id=weapon['main_weapon_id'],used_before=before,
                used_after=used_ammo(unit,weapon),unlimited=unlimited)


def use_map_weapon(unit,weapon,records,*,en_cost,mp_cost,hp_cost,predicting=False):
    """Low-level UseMapWeapon mutator; eligibility and cost generation are external.

    MapWeaponServiceBase.Execute calls this BEFORE MapWeaponExecutor.Execute
    (0x5f5f050, then 0x5f5f0cc). Contexts dependent on current HP/EN must
    therefore be constructed after payment. Callers check availability first.
    """
    from .weapon_use import use_weapon
    result=use_weapon(unit,en_cost,mp_cost,hp_cost,predicting=predicting)
    if not predicting:result['map_ammo']=consume_map_ammo(unit,weapon,records)
    return result


def recover_map_ammo(unit,value):
    changes=[]
    counts=unit.get('map_weapon_used_counts',{})
    for main_id,before in list(counts.items()):
        after=max(0,i32(before-value));counts[main_id]=after
        changes.append(dict(main_weapon_id=int(main_id),used_before=before,used_after=after))
    return changes


def can_recover_map_ammo(unit,all_form_map_weapons):
    # Native searches all forms without the escape chain, rather than every
    # historical dictionary key. The complete weapon set is a caller input.
    return any(used_ammo(unit,weapon)>0 for weapon in all_form_map_weapons)
