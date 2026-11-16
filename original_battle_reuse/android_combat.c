/* Execute the original synchronous main attack and counter on live game state. */

#include "android_runtime.h"

extern void *dlsym(void *library, const char *name);
extern int printf(const char *format, ...);
extern int fflush(void *stream);

struct combat_api {
    void *(*object_class)(void *);
    void *(*unbox)(void *);
    void *(*get_field)(void *, const char *);
    void *(*field_type)(void *);
    void *(*class_from_type)(void *);
    void (*set_object_field)(void *, void *, void *);
};

struct contender {
    void *unit;
    void *id;
    void *coord;
    void *terrain;
};

struct strike_pair {
    struct contender *attacker;
    struct contender *target;
    long long weapon;
    long long counter_weapon;
    int attacker_number;
    int target_number;
    int predicted_damage;
    int target_hp;
};

static int get(const struct combat_api *api, void *object, const char *name, void **value) {
    return object && call(api->object_class(object), name, 0, object, 0, value) && *value;
}

static struct combat_services create_combat_services(const struct battle_images *images,
                                                      const struct battle_session *session, void *memento) {
    const struct combat_services failed = {0};
    struct combat_services services = {0};
    void *master = session->master_repository;
    void *map = session->map_repository;
    void *traits = session->trait_service;
    void *units = session->unit_repository;
    void *turns = session->turn_repository;
    void *context = session->context_repository;
    if (!memento) {
        void *memento_class = get_class(images->application, "Eternal.Application", "IngameMementoManager");
        void *memento_args[] = {
            0, 0, 0, 0, context, session->supporter_repository, session->trait_repository,
            units, map, master, 0, traits, turns, 0, 0, 0, session->aura_service,
        };
        memento = construct(memento_class, 17, memento_args);
    }
    services.memento = memento;
    void *escape_class = get_class(images->domain, "Eternal.Domain", "CanEscapeSpecification");
    void *escape_args[] = {master, map};
    services.can_escape = construct(escape_class, 2, escape_args);
    if (!services.memento || !services.can_escape) {
        return failed;
    }
    void *calculator_class = get_class(images->domain, "Eternal.Domain", "BattleCalculateService");
    if (battle_client_28) {
        void *splash_args[] = {traits, map, units, master};
        void *splash_class = get_class(images->domain, "Eternal.Domain", "SplashDamageCalculateService");
        services.splash_damage = construct(splash_class, 4, splash_args);
        if (!services.splash_damage) {
            return failed;
        }
    }
    void *calculator_args[] = {
        services.memento, map, master, traits, session->random_repository, turns, services.can_escape, services.splash_damage,
    };
    services.calculator = construct(calculator_class, battle_client_28 ? 8 : 7, calculator_args);
    services.prediction_rule = construct(get_class(images->domain, "Eternal.Domain", "AIAttackPredictionSelectRule"), 0, 0);
    void *available_class = get_class(images->domain, "Eternal.Domain", "AvailableWeaponService");
    void *available_args[] = {units, map, context, turns, services.calculator, traits, services.prediction_rule};
    services.availability = services.prediction_rule && services.calculator ? construct(available_class, 7, available_args) : 0;
    void *kill_class = get_class(images->domain, "Eternal.Domain", "KillLogService");
    void *kill_args[] = {context, turns, units};
    services.kill_log = construct(kill_class, 3, kill_args);
    void *skill_class = get_class(images->domain, "Eternal.Domain", "IngameSkillService");
    void *skill_args[] = {services.memento, units, traits, turns};
    services.skills = construct(skill_class, 4, skill_args);
    void *attack_class = get_class(images->domain, "Eternal.Domain", "AttackService");
    void *attack_args[] = {context, services.skills, traits, services.kill_log, services.can_escape, map, units};
    services.attack = services.skills && services.kill_log
        ? construct(attack_class, battle_client_28 ? 7 : 6, attack_args) : 0;
    return services;
}

static int load_contenders(const struct combat_api *api, const struct battle_images *images,
                           const struct battle_session *session, struct contender *contenders) {
    void *map = 0, *origins = 0;
    if (!get(api, session->map_repository, "FindLatest", &map) ||
        !get(api, map, "get_UnitMapOriginPlaceDict", &origins)) {
        return 0;
    }
    void *unit_class = api->object_class(session->unit_repository);
    void *origins_class = api->object_class(origins);
    void *id_class = get_class(images->domain, "Eternal.Domain", "IngameUnitId");
    for (int number = 1; number <= session->unit_count; ++number) {
        struct contender *candidate = &contenders[number - 1];
        void *id_args[] = {&number};
        candidate->id = construct(id_class, 1, id_args);
        void *lookup[] = {candidate->id};
        if (!candidate->id || !call(unit_class, "FindById", 1, session->unit_repository, lookup, &candidate->unit) ||
            !get(api, candidate->unit, "get_StandbyTerrainType", &candidate->terrain) ||
            !call(origins_class, "FindByUnitId", 1, origins, lookup, &candidate->coord)) {
            return 0;
        }
    }
    return 1;
}

static int available_weapons(const struct combat_api *api, void *availability, const struct contender *attacker,
                             const struct contender *target, void **weapons, int *size) {
    void *arguments[] = {attacker->unit, attacker->coord, attacker->terrain, target->unit, target->coord};
    void *count = 0;
    /* The boxed coordinate supplies the original Nullable<Coord> parameter. */
    if (!call_boxed(api->object_class(availability), "FindAvailableWeapons", 5, availability, arguments, weapons) ||
        !get(api, *weapons, "Count", &count)) {
        return 0;
    }
    *size = *(int *) api->unbox(count);
    return 1;
}

static int ordinary_weapon_at(const struct combat_api *api, void *weapons, int index, long long *weapon_id) {
    void *weapon_args[] = {&index};
    void *weapon = 0, *preemptive = 0, *id = 0;
    *weapon_id = 0;
    if (!call(api->object_class(weapons), "FindByIndex", 1, weapons, weapon_args, &weapon) ||
        !get(api, weapon, "IsPreemptiveAttack", &preemptive) || !get(api, weapon, "get_WeaponId", &id)) {
        return 0;
    }
    if (!*(unsigned char *) api->unbox(preemptive)) {
        *weapon_id = *(long long *) api->unbox(id);
    }
    return 1;
}

static int first_ordinary_weapon(const struct combat_api *api, void *availability, const struct contender *attacker,
                                 const struct contender *target, long long *weapon_id) {
    void *weapons = 0;
    int size = 0;
    *weapon_id = 0;
    if (!available_weapons(api, availability, attacker, target, &weapons, &size)) {
        return 0;
    }
    for (int index = 0; index < size; ++index) {
        if (!ordinary_weapon_at(api, weapons, index, weapon_id)) {
            return 0;
        }
        if (*weapon_id) {
            return 1;
        }
    }
    return 1;
}

static int predict_main(const struct combat_api *api, void *calculator, const struct contender *attacker,
                        const struct contender *target, long long weapon, int counter_type,
                        int *damage, int *target_hp) {
    void *arguments[] = {attacker->unit, api->unbox(attacker->coord), &weapon, target->unit, 0, &counter_type};
    void *result = 0, *predicted = 0, *hp = 0;
    if (!call(api->object_class(calculator), "PredictMainAttackTentatively", 6, calculator, arguments, &result) ||
        !get(api, result, "get_HpTotalDamage", &predicted) || !get(api, target->unit, "get_CurrentHp", &hp)) {
        return 0;
    }
    *damage = *(int *) api->unbox(predicted);
    *target_hp = *(int *) api->unbox(hp);
    return 1;
}

static int select_pair(const struct combat_api *api, const struct combat_services *services, int counter_type,
                       struct contender *contenders, int count, struct strike_pair *pair) {
    struct strike_pair fallback = {0};
    for (int i = 0; i < FIXTURE_PLAYER_UNITS; ++i) {
        struct contender *attacker = &contenders[i];
        if (!attacker->coord) {
            continue;
        }
        for (int j = FIXTURE_PLAYER_UNITS; j < count; ++j) {
            struct contender *target = &contenders[j];
            if (!target->coord) {
                continue;
            }
            void *weapons = 0;
            int size = 0;
            long long counter_weapon = 0;
            if (!available_weapons(api, services->availability, attacker, target, &weapons, &size)) {
                return 0;
            }
            if (!size) {
                continue;
            }
            if (!first_ordinary_weapon(api, services->availability, target, attacker, &counter_weapon)) {
                return 0;
            }
            if (!counter_weapon) {
                continue;
            }
            for (int index = 0; index < size; ++index) {
                long long weapon = 0;
                if (!ordinary_weapon_at(api, weapons, index, &weapon)) {
                    return 0;
                }
                if (!weapon) {
                    continue;
                }
                struct strike_pair candidate = {
                    .attacker = attacker, .target = target, .weapon = weapon, .counter_weapon = counter_weapon,
                    .attacker_number = i + 1, .target_number = j + 1,
                };
                if (!predict_main(api, services->calculator, attacker, target, weapon, counter_type,
                                  &candidate.predicted_damage, &candidate.target_hp)) {
                    return 0;
                }
                /* Leave room for damage variation so the fixed-stage probe can exercise the counter. */
                if (candidate.predicted_damage > 0 && candidate.predicted_damage < candidate.target_hp / 2) {
                    *pair = candidate;
                    return 1;
                }
                if (!fallback.attacker || ((long long) candidate.predicted_damage * fallback.target_hp <
                    (long long) fallback.predicted_damage * candidate.target_hp)) {
                    fallback = candidate;
                }
            }
        }
    }
    if (fallback.attacker) {
        *pair = fallback;
        return 1;
    }
    printf("REUSE no_mutually_available_attack\n");
    return 1;
}

static void *prepare_strike_step(const struct combat_api *api, const struct battle_images *images,
                                 const struct battle_session *session, struct strike_pair *pair) {
    void *turn = 0;
    if (!get(api, session->turn_repository, "FindLatest", &turn)) {
        return 0;
    }
    void *factory_class = get_class(images->domain, "Eternal.Domain", "StepUnitPossibleDataFactory");
    void *factory_args[] = {
        session->map_repository, session->unit_repository, session->turn_repository, session->trait_service,
    };
    void *factory = construct(factory_class, 4, factory_args);
    void *create_args[] = {pair->attacker->id};
    void *possible = 0;
    if (!factory || !call(factory_class, "Create", 1, factory, create_args, &possible) || !possible) {
        return 0;
    }
    void *next_step_args[] = {pair->attacker->unit, possible, api->unbox(pair->attacker->coord)};
    void *unused = 0, *step = 0, *attack_targets = 0;
    if (!call(api->object_class(turn), "CreateNextStep", 3, turn, next_step_args, &unused) ||
        !get(api, turn, "GetCurrentStep", &step) || !get(api, step, "get_AttackTargetUnits", &attack_targets)) {
        return 0;
    }
    void *target_args[] = {pair->target->id};
    void *selection_class = get_class(images->domain, "Eternal.Domain", "UnitCounterSelection");
    void *counter_args[] = {&pair->counter_weapon};
    void *selection = 0;
    if (!call(api->object_class(attack_targets), "SetAttackTargetUnitId", 1, attack_targets, target_args, &unused) ||
        !call(selection_class, "CreateCounter", 1, 0, counter_args, &selection) || !selection) {
        return 0;
    }
    void *selection_args[] = {api->unbox(selection)};
    void *weapon_args[] = {&pair->weapon};
    void *key = 0, *random = 0;
    if (!call(api->object_class(step), "SetCounterSelection", 1, step, selection_args, &unused) ||
        !call(api->object_class(pair->attacker->unit), "SelectWeapon", 1, pair->attacker->unit, weapon_args, &unused) ||
        !get(api, turn, "GetCurrentTurnPhaseStepKey", &key) || !get(api, session->random_repository, "Get", &random)) {
        return 0;
    }
    void *key_args[] = {key};
    return call(api->object_class(random), "InitStep", 1, random, key_args, &unused) ? step : 0;
}

static int get_resources(const struct combat_api *api, const struct contender *unit, int values[3]) {
    void *hp = 0, *en = 0, *mp = 0;
    if (!get(api, unit->unit, "get_CurrentHp", &hp) || !get(api, unit->unit, "get_CurrentEn", &en) ||
        !get(api, unit->unit, "GetCurrentMp", &mp)) {
        return 0;
    }
    values[0] = *(int *) api->unbox(hp);
    values[1] = *(int *) api->unbox(en);
    values[2] = *(int *) api->unbox(mp);
    return 1;
}

static int set_reference(const struct combat_api *api, void *object, const char *name, void *value) {
    void *field = api->get_field(api->object_class(object), name);
    if (!field || !value) {
        return 0;
    }
    api->set_object_field(object, field, value);
    return 1;
}

static int prepare_attack_state(const struct combat_api *api, const struct battle_session *session,
                                void *use_case, void *step) {
    void *turn = 0, *map = 0, *context = 0;
    if (!get(api, session->turn_repository, "FindLatest", &turn) ||
        !get(api, session->map_repository, "FindLatest", &map) ||
        !get(api, session->context_repository, "Get", &context) ||
        !set_reference(api, use_case, "_turnActivity", turn) ||
        !set_reference(api, use_case, "_stepActivity", step) ||
        !set_reference(api, use_case, "_mapRootEntity", map) ||
        !set_reference(api, use_case, "_ingameContextEntity", context)) {
        return 0;
    }
    /* ExecuteAsync allocates these four lists before either strike helper runs. */
    const char *lists[] = {
        "_player1SideWeaponHitUnits", "_player2SideWeaponHitUnits",
        "_player1SideDamagedUnits", "_player2SideDamagedUnits",
    };
    void *klass = api->object_class(use_case);
    for (unsigned int i = 0; i < sizeof(lists) / sizeof(lists[0]); ++i) {
        void *field = api->get_field(klass, lists[i]);
        void *type = field ? api->field_type(field) : 0;
        void *list = type ? construct(api->class_from_type(type), 0, 0) : 0;
        if (!list) {
            return 0;
        }
        api->set_object_field(use_case, field, list);
    }
    return 1;
}

static int apply_strikes(const struct combat_api *api, const struct battle_images *images,
                          const struct battle_session *session, const struct combat_services *services,
                          struct strike_pair *pair, void *step) {
    void *factory_class = get_class(images->domain, "Eternal.Domain", "BattleParticipationStatusFactory");
    void *factory_args[] = {session->unit_repository};
    void *factory = construct(factory_class, 1, factory_args);
    void *participation_args[] = {pair->attacker->unit, pair->target->unit, step};
    void *participation = 0;
    if (!factory || !call(factory_class, "Create", 3, factory, participation_args, &participation) || !participation) {
        return 0;
    }
    void *use_case_class = get_class(images->application, "Eternal.Application", "AttackUseCase");
    void *constructor_args[29] = {
        [1] = services->attack, [4] = services->calculator, [5] = session->context_repository,
        [6] = services->memento, [7] = session->unit_repository, [8] = session->map_repository,
        [9] = session->master_repository, [12] = session->trait_service, [13] = session->turn_repository,
        [15] = services->kill_log, [23] = services->can_escape, [26] = session->date_time,
    };
    void *use_case = construct(use_case_class, 29, constructor_args);
    int attacker_before[3], target_before[3], attacker_after[3], target_after[3];
    if (!use_case || !prepare_attack_state(api, session, use_case, step) ||
        !get_resources(api, pair->attacker, attacker_before) || !get_resources(api, pair->target, target_before)) {
        return 0;
    }
    unsigned char preemptive = 0;
    void *main_args[] = {
        &preemptive, pair->attacker->unit, api->unbox(pair->attacker->coord), &pair->weapon,
        pair->target->unit, api->unbox(pair->target->coord), 0, participation,
    };
    void *main_result = 0, *can_attack = 0, *hit = 0, *damage = 0;
    printf("REUSE original_main_strike_enter\n");
    fflush(0);
    if (!call(use_case_class, "_ExecuteMainAttack", 8, use_case, main_args, &main_result) ||
        !get(api, main_result, "CanAttack", &can_attack) || !get(api, main_result, "get_IsHit", &hit) ||
        !get(api, main_result, "get_HpTotalDamage", &damage) ||
        !get_resources(api, pair->attacker, attacker_after) || !get_resources(api, pair->target, target_after)) {
        return 0;
    }
    int executed = *(unsigned char *) api->unbox(can_attack);
    printf("REUSE original_main_strike_return executed %d hit %u damage %lld attacker_en %d->%d target_hp %d->%d\n",
           executed, *(unsigned char *) api->unbox(hit), *(long long *) api->unbox(damage),
           attacker_before[1], attacker_after[1], target_before[0], target_after[0]);
    fflush(0);
    if (!executed) {
        return 0;
    }
    void *counter_args[] = {
        &preemptive, pair->target->unit, api->unbox(pair->target->coord), pair->attacker->unit,
        api->unbox(pair->attacker->coord), 0, participation,
    };
    void *counter_result = 0, *can_counter = 0;
    int counter_attacker_after[3], counter_target_after[3];
    printf("REUSE original_counter_strike_enter\n");
    fflush(0);
    if (!call(use_case_class, "_ExecuteCounter", 7, use_case, counter_args, &counter_result)) {
        return 0;
    }
    /* The original early exit for a defeated target returns a null reference. */
    if (!counter_result) {
        printf("REUSE original_counter_strike_return executed 0 result none\n");
        return 1;
    }
    if (!get(api, counter_result, "CanCounter", &can_counter) || !get(api, counter_result, "get_IsHit", &hit) ||
        !get(api, counter_result, "get_HpTotalDamage", &damage) ||
        !get_resources(api, pair->target, counter_attacker_after) ||
        !get_resources(api, pair->attacker, counter_target_after)) {
        return 0;
    }
    printf("REUSE original_counter_strike_return executed %u hit %u damage %lld counter_en %d->%d attacker_hp %d->%d\n",
           *(unsigned char *) api->unbox(can_counter), *(unsigned char *) api->unbox(hit),
           *(long long *) api->unbox(damage), target_after[1], counter_attacker_after[1],
           attacker_after[0], counter_target_after[0]);
    return 1;
}

static int bind_combat_api(void *library, struct combat_api *api) {
    *api = (struct combat_api) {
        .object_class = dlsym(library, "il2cpp_object_get_class"),
        .unbox = dlsym(library, "il2cpp_object_unbox"),
        .get_field = dlsym(library, "il2cpp_class_get_field_from_name"),
        .field_type = dlsym(library, "il2cpp_field_get_type"),
        .class_from_type = dlsym(library, "il2cpp_class_from_type"),
        .set_object_field = dlsym(library, "il2cpp_field_set_value_object"),
    };
    return api->object_class && api->unbox && api->get_field && api->field_type &&
        api->class_from_type && api->set_object_field;
}

static int prepare_opening_step(void *library, const struct battle_images *images,
                                const struct battle_session *session, const struct combat_api *api,
                                const struct combat_services *services, struct contender *contenders,
                                struct strike_pair *pair, void **step) {
    void (*get_static_field)(void *, void *) = dlsym(library, "il2cpp_field_static_get_value");
    void *counter_class = get_class(images->domain, "Eternal.Domain", "UnitCounterSelectionType");
    void *counter_field = counter_class ? api->get_field(counter_class, "Counter") : 0;
    int counter_type = 0;
    if (!get_static_field || !counter_field) {
        return 0;
    }
    get_static_field(counter_field, &counter_type);
    if (!load_contenders(api, images, session, contenders) ||
        !select_pair(api, services, counter_type, contenders, session->unit_count, pair)) {
        return 0;
    }
    if (!pair->attacker) {
        return 1;
    }
    printf("REUSE original_available_weapons attacker %d target %d main %lld counter %lld predicted_damage %d target_hp %d\n",
           pair->attacker_number, pair->target_number, pair->weapon, pair->counter_weapon, pair->predicted_damage, pair->target_hp);
    fflush(0);
    *step = prepare_strike_step(api, images, session, pair);
    if (!*step) {
        return 0;
    }
    printf("REUSE original_action_step_selected\n");
    fflush(0);
    return 1;
}

int select_opening_attack(void *library, const struct battle_images *images, const struct battle_session *session,
                          const struct combat_services *combat, struct player_attack *attack) {
    struct combat_api api;
    struct contender contenders[session->unit_count];
    struct strike_pair pair = {0};
    void *step = 0;
    if (!bind_combat_api(library, &api) ||
        !prepare_opening_step(library, images, session, &api, combat, contenders, &pair, &step)) {
        return 0;
    }
    if (step) {
        *attack = (struct player_attack) {pair.attacker->unit, pair.target->unit, pair.weapon};
    }
    return 1;
}

int run_battle_action(void *library, const struct battle_images *images, const struct battle_session *session,
                      enum battle_action_mode action) {
    struct battle_exchange exchange = {0};
    if (action != BATTLE_STRIKES && !initialize_exchange(library, images, session, &exchange)) {
        return 0;
    }
    struct combat_services services = create_combat_services(images, session, exchange.memento);
    if (!services.availability || !services.attack) {
        return 0;
    }
    if (action != BATTLE_STRIKES) {
        return execute_exchange(library, images, session, &exchange, &services, action);
    }
    struct combat_api api;
    struct contender contenders[session->unit_count];
    struct strike_pair pair = {0};
    void *step = 0;
    return bind_combat_api(library, &api) &&
        prepare_opening_step(library, images, session, &api, &services, contenders, &pair, &step) &&
        step && apply_strikes(&api, images, session, &services, &pair, step);
}
