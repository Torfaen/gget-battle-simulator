"""Original ordinary CalculateBattleResult orchestration over explicit services.

No weapon use or damage reflection occurs here. Raw result fields retain the
calculated potential values; the original result getters apply hit/parry gates.
Dependencies must supply current repository/trait/weapon/map state.
"""
from .original import i32
from .battle_calculation_entry import prepare_battle_calculation


def calculate_battle_result(services,attacker,attack_coord,weapon_id,main_target,target_coord,
                           counter_type,guard=None,participation=None,*,keep_weapon_traits=False,
                           prediction=False):
    s=services
    entry=prepare_battle_calculation(s,attacker,attack_coord,weapon_id,main_target,target_coord,
        counter_type,guard,participation,prediction=prediction)
    if entry.inability_result is not None:return entry.inability_result
    weapon=entry.weapon;target=entry.damaged_unit
    terrain=s.terrain_list(entry.map_root,main_target,target_coord)
    hit_rate=s.calculate_hit_rate(attacker,weapon_id,main_target,terrain,i32(counter_type))
    step=s.current_step(s.current_turn())
    if s.show_tutorial_support_defend(step) and prediction:
        hit_rate=s.make_hit_rate(max(30,s.hit_rate_value(hit_rate)),False,False)
    else:
        tutorial=s.show_tutorial_support_defend(step)
        if guard is not None and tutorial:hit_rate=s.make_hit_rate(100,False,False)
    hit=s.evaluate_hit(hit_rate,entry.target_is_broken,prediction)
    parry=False
    if hit:
        if not s.hit_is_sure(hit_rate) and not s.hit_is_fixed(hit_rate):
            if not s.is_action_inability(main_target):parry=s.evaluate_parry(main_target,prediction)
        hit=not parry
    main_terrain=s.standby_terrain(main_target)
    config=s.damage_config()
    container=s.create_damage_container(attacker,weapon,target,entry.counter_type,main_terrain,config)
    critical=s.evaluate_critical(attacker,weapon,participation,prediction)
    distance=s.calc_distance(attacker,attack_coord,main_target,target_coord)
    power=s.weapon_power(attacker,weapon,distance)
    damage_cut=s.has_damage_cut(target) if hit else False
    kind=s.critical_type(critical)
    hp_damage,barrier_damage=s.total_damage(container,power,kind,1)
    hp_damage=i32(hp_damage);barrier_damage=i32(barrier_damage)
    hp_for_break=hp_damage if hit else 0
    en_damage=s.en_attack(weapon,s.max_en(target))
    if not keep_weapon_traits:s.disable_weapon_traits(entry.refresh_units)
    debuffs=None
    if hit:
        identity=s.unit_id(attacker)
        traits=s.weapon_traits(weapon)
        debuffs=s.evaluate_weapon_debuffs(identity,traits,participation)
    mp_damage=s.mp_attack(weapon) if s.mp_change_enabled(target) else None
    drained_hp=s.drain_hp(weapon,i32(hp_damage+barrier_damage),s.max_hp(attacker))
    drained_en=s.drain_en(weapon,0 if en_damage is None else en_damage,s.max_en(attacker))
    en_cost=s.en_consumption(weapon)
    mp_cost=s.mp_consumption(attacker,weapon)
    hp_cost=s.hp_consumption(weapon,s.max_hp(attacker))
    broken=False
    if not entry.target_is_broken and not s.is_unbreakable(target):
        broken=i32(s.current_hp(target)-hp_for_break)<=0
    hit_result=s.hit_result(hit,parry,hit_rate)
    correction=s.correction_damage_rate(attacker,weapon,target)
    result=s.battle_result(hp_damage=hp_damage,hp_barrier_damage=barrier_damage,
        en_damage=0 if en_damage is None else en_damage,mp_damage=0 if mp_damage is None else mp_damage,
        drained_hp=drained_hp,drained_en=drained_en,attack_weapon=weapon,attack_weapon_power=power,
        attack_weapon_en_consumption=en_cost,attack_weapon_mp_consumption=mp_cost,
        attack_weapon_hp_consumption=hp_cost,attack_target_is_broken=broken,
        attack_target_counter_selection_type=i32(counter_type),is_support_defend=guard is not None,
        hit_result=hit_result,critical_rate=critical,correction_damage_rate=correction,
        passive_weapon_trait_ids=entry.applied_passive_ids,debuff_weapon_trait_ids=debuffs,has_damage_cut=damage_cut)
    s.save_result(s.unit_id(attacker),result)
    return result
