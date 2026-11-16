/* Run the original NPC initialization against the constructed scene. */

#include "android_runtime.h"

extern void *dlsym(void *library, const char *name);
extern int printf(const char *format, ...);
extern int fflush(void *stream);
extern void *fopen(const char *name, const char *mode);
extern int fscanf(void *file, const char *format, ...);
extern int fclose(void *file);

struct player_selection {
    long long user_id;
    int formation_type;
    long long content_target;
};

struct deployment {
    void *initializer;
    void *formation;
    void *supporter_repository;
    struct battle_session session;
};

static int uses_custom_formation(void);

static void *new_domain(const struct battle_images *images, const char *name, int count, void **arguments) {
    return construct(get_class(images->domain, "Eternal.Domain", name), count, arguments);
}

static void *new_infrastructure(const struct battle_images *images, const char *name, int count,
                                void **arguments) {
    return construct(get_class(images->infrastructure, "Eternal.Infrastructure", name), count, arguments);
}

static void *initialize_user_store(void *library, const struct battle_images *images, int load_player) {
    void *(*get_field)(void *, const char *) = dlsym(library, "il2cpp_class_get_field_from_name");
    void (*get_field_value)(void *, void *, void *) = dlsym(library, "il2cpp_field_get_value");
    if (!get_field || !get_field_value) {
        return 0;
    }
    void *store_class = get_class(images->infrastructure, "Eternal.Infrastructure", "EternalUserDataStore");
    void *helper_class = get_class(images->infrastructure, "Eternal.Infrastructure", "UserDatabaseHelper");
    void *store = 0;
    void *unused = 0;
    if (!call(store_class, "_InitializeUserDataStore", 0, 0, 0, &unused) ||
        !call(store_class, "get_Instance", 0, 0, 0, &store) || !store) {
        return 0;
    }
    void *helper_field = get_field(store_class, "_helper");
    void *helper = 0;
    if (helper_field) {
        get_field_value(store, helper_field, &helper);
    }
    if (!helper) {
        return 0;
    }
    if (!load_player) {
        return call(helper_class, "Setup", 0, helper, 0, &unused) ? store : 0;
    }
    long length = 0;
    void *data = read_managed_bytes(library, battle_path("stage/PlayerDatabase.bytes"), &length);
    void *database_class = get_class(images->infrastructure, "Eternal.Infrastructure", "EternalUserMemoryDatabase");
    unsigned char intern_strings = 1;
    int workers = 1;
    void *database_args[] = {data, &intern_strings, 0, &workers};
    void *database = data ? construct(database_class, 4, database_args) : 0;
    void *set_args[] = {database};
    if (!database || !call(helper_class, "set_DB", 1, helper, set_args, &unused)) {
        return 0;
    }
    printf("REUSE original_player_database_loaded %ld\n", length);
    return store;
}

static int bind_local_user(void *library, const struct battle_images *images, void *user_info, long long local_id) {
    void *(*get_field)(void *, const char *) = dlsym(library, "il2cpp_class_get_field_from_name");
    void (*set_object_field)(void *, void *, void *) = dlsym(library, "il2cpp_field_set_value_object");
    if (!user_info || !get_field || !set_object_field) {
        return 0;
    }
    void *info_class = get_class(images->infrastructure, "Eternal.Infrastructure", "UserInfoRepository");
    void *id_field = get_field(info_class, "_userId");
    void *id_args[] = {&local_id};
    void *id = new_domain(images, "UserId", 1, id_args);
    if (!id || !id_field) {
        return 0;
    }
    set_object_field(user_info, id_field, id);
    return 1;
}

static void *create_player_formation(const struct battle_images *images, const struct player_selection *selection,
                                     void *store, void *master, void *info, void *characters, void *date_time,
                                     struct battle_session *session) {
    void *supporter_args[] = {store, info, master};
    void *supporters = new_infrastructure(images, "UserSupporterRepository", 3, supporter_args);
    void *part_args[] = {store, master, info};
    void *parts = new_infrastructure(images, "OptionPartsRepository", 3, part_args);
    void *record_args[] = {master, store, info};
    void *records = new_infrastructure(images, "UserRecordRepository", 3, record_args);
    if (!supporters || !parts || !records) {
        return 0;
    }
    void *unit_args[] = {store, parts, master, info, supporters, records, date_time};
    void *units = new_infrastructure(images, "UserUnitRepository", 7, unit_args);
    void *formation_class = get_class(images->infrastructure, "Eternal.Infrastructure", "UserGroupFormationRepository");
    void *repository_args[] = {store, units, characters, master, info, supporters};
    void *repository = units ? construct(formation_class, 6, repository_args) : 0;
    session->user_unit_repository = units;
    session->user_group_formation_repository = repository;
    session->user_supporter_repository = supporters;
    session->user_record_repository = records;
    session->option_parts_repository = parts;
    int type = selection->formation_type;
    long long target = selection->content_target;
    /* Nullable<bool>: enable the recorded sortie form for local formations. */
    unsigned short auto_form_change = uses_custom_formation() ? 0x0101 : 0;
    void *formation_args[] = {&type, &target, &auto_form_change};
    void *formation = 0;
    return repository && call(formation_class, "CreateUserGroupFormationEntity", 3, repository,
                              formation_args, &formation) ? formation : 0;
}

static struct deployment create_initializer(void *library, const struct battle_images *images, void *unit_repository,
                                             void *master_repository, void *user_store,
                                             const struct player_selection *selection) {
    const struct deployment failed = {0};
    void *clock_class = get_class(images->application, "Eternal.Application", "EternalDateTimeService");
    void *date_time = construct(clock_class, 0, 0);
    long long now = wallclock_milliseconds();
    void *time_args[] = {&now};
    void *unused = 0;
    if (!date_time || !now || !call(clock_class, "UpdateServerSyncTime", 1, date_time, time_args, &unused)) {
        return failed;
    }
    void *user_info_args[] = {user_store, master_repository, 0, date_time, 0};
    void *user_info = new_infrastructure(images, "UserInfoRepository", 5, user_info_args);
    if (!bind_local_user(library, images, user_info, selection ? selection->user_id : 1)) {
        return failed;
    }
    void *character_args[] = {user_store, master_repository, user_info, date_time};
    void *characters = 0;
    if (battle_client_28) {
        void *record_args[] = {master_repository, user_store, user_info};
        void *records = new_infrastructure(images, "UserRecordRepository", 3, record_args);
        void *stage_args[] = {master_repository, user_store, user_info, 0, 0, 0, date_time, 0, 0};
        void *user_stage = new_infrastructure(images, "UserStageRepository", 9, stage_args);
        void *function_args[] = {user_store, master_repository, user_info, user_stage, records, date_time};
        void *functions = new_infrastructure(images, "UserGameFunctionReleaseRepository", 6, function_args);
        void *character_args_28[] = {user_store, master_repository, user_info, functions, date_time};
        characters = functions ? new_infrastructure(images, "UserCharacterRepository", 5, character_args_28) : 0;
    } else {
        characters = new_infrastructure(images, "UserCharacterRepository", 4, character_args);
    }
    void *capture_args[] = {user_store, user_info};
    void *capture = new_infrastructure(images, "UserCaptureLotteryResultRepository", 2, capture_args);
    if (!user_info || !characters || !capture) {
        return failed;
    }

    void *map_root = new_infrastructure(images, "InMemoryMapRootRepository", 0, 0);
    void *trait_repository = new_infrastructure(images, "InMemoryIngameTraitSetRepository", 0, 0);
    void *random_repository = new_infrastructure(images, "InMemoryStageRandomRepository", 0, 0);
    void *turn_repository = new_infrastructure(images, "InMemoryTurnActivityRepository", 0, 0);
    void *trait_owners = new_infrastructure(images, "InMemoryNonUnitTraitOwnerRepository", 0, 0);
    void *area_args[] = {master_repository};
    void *area_repository = new_infrastructure(images, "InMemoryUnitReinforcementAreaRepository", 1, area_args);
    if (!map_root || !trait_repository || !random_repository || !turn_repository ||
        !trait_owners || !area_repository) {
        return failed;
    }

    void *trait_condition_args[] = {trait_repository, unit_repository, trait_owners};
    void *trait_conditions = new_domain(images, "TraitConditionAllChecker", 3, trait_condition_args);
    void *trait_boost_args[] = {unit_repository};
    void *trait_boost = new_domain(images, "TraitBoostConditionChecker", 1, trait_boost_args);
    void *trait_turn = new_domain(images, "TraitTurnProgressedSpecification", 0, 0);
    void *reinforcement_args[] = {map_root, unit_repository, area_repository};
    void *reinforcements = new_domain(images, "AreaReinforcementService", 3, reinforcement_args);
    void *sequencer = new_domain(images, "IngameUniqueNumberSequencer", 0, 0);
    if (!trait_conditions || !trait_boost || !trait_turn || !reinforcements || !sequencer) {
        return failed;
    }
    void *trait_args[] = {trait_repository, unit_repository, master_repository, random_repository,
                          turn_repository, trait_owners, map_root, trait_conditions, trait_boost, trait_turn};
    void *traits = new_domain(images, "IngameTraitSetService", 10, trait_args);
    void *convert_args[] = {traits};
    void *convert = traits ? new_domain(images, "IngameUnitConvertService", 1, convert_args) : 0;
    void *facade_args[] = {master_repository, characters, sequencer, capture, convert};
    void *facade = convert ? new_domain(images, "NpcToIngameUnitConverterFacade", 5, facade_args) : 0;
    if (!facade) {
        return failed;
    }

    struct deployment deployment = {0};
    deployment.session = (struct battle_session) {
        .unit_repository = unit_repository,
        .master_repository = master_repository,
        .map_repository = map_root,
        .turn_repository = turn_repository,
        .random_repository = random_repository,
        .trait_repository = trait_repository,
        .user_data_store = user_store,
        .user_info_repository = user_info,
        .user_character_repository = characters,
        .trait_service = traits,
        .trait_condition_checker = trait_conditions,
        .non_unit_trait_owners = trait_owners,
        .reinforcements = reinforcements,
        .unit_number_sequencer = sequencer,
        .date_time = date_time,
    };
    void *supporter_service = 0;
    void *player_converter = 0;
    if (selection) {
        deployment.formation = create_player_formation(images, selection, user_store, master_repository,
                                                       user_info, characters, date_time,
                                                       &deployment.session);
        if (!deployment.formation) {
            return failed;
        }
        printf("REUSE native_player_formation_created\n");
        fflush(0);
        deployment.supporter_repository = new_infrastructure(images, "InMemoryIngameSupporterRepository", 0, 0);
        deployment.session.supporter_repository = deployment.supporter_repository;
        void *selector_args[] = {unit_repository, map_root, traits};
        void *selector = new_domain(images, "SupporterSkillTargetSelector", 3, selector_args);
        void *supporter_args[] = {deployment.supporter_repository, unit_repository, map_root, traits,
                                  trait_owners, turn_repository, master_repository, selector};
        void *supporter_class = get_class(images->application, "Eternal.Application", "IngameSupporterService");
        supporter_service = construct(supporter_class, 8, supporter_args);
        void *player_args[] = {master_repository, sequencer, convert};
        player_converter = new_domain(images, "UserUnitToIngameUnitConverter", 3, player_args);
        if (!deployment.supporter_repository || !selector || !supporter_service || !player_converter) {
            return failed;
        }
    }
    /* The fixed Eternal Road stage uses these two setup methods directly. Its
       initialization has no trial formation or map-event account service. */
    void *initializer_args[] = {unit_repository, supporter_service, 0, facade, player_converter, master_repository,
                                date_time, 0, reinforcements};
    void *initializer_class = get_class(images->application, "Eternal.Application", "DefaultUnitInitializer");
    deployment.initializer = construct(initializer_class, 9, initializer_args);
    return deployment;
}

static int check_npc_units(void *library, const struct battle_images *images, void *repository,
                           int expected_count, int npc_count) {
    void *(*unbox)(void *) = dlsym(library, "il2cpp_object_unbox");
    void *(*object_class)(void *) = dlsym(library, "il2cpp_object_get_class");
    if (!unbox || !object_class) {
        return 0;
    }
    void *repository_class = object_class(repository);
    void *npc_id_class = get_class(images->domain, "Eternal.Domain", "NpcId");
    void *unit_id_class = get_class(images->domain, "Eternal.Domain", "IngameUnitId");
    void *units = 0;
    void *count = 0;
    if (!call(repository_class, "FindAll", 0, repository, 0, &units) || !units ||
        !call(object_class(units), "Count", 0, units, 0, &count) || !count) {
        return 0;
    }
    int unit_count = *(int *) unbox(count);
    printf("REUSE native_unit_count %d\n", unit_count);
    void *expected = fopen(battle_path("stage/NpcExpected.tsv"), "r");
    if (!expected || unit_count != expected_count) {
        if (expected) {
            fclose(expected);
        }
        return 0;
    }
    int seen[npc_count];
    int checked = 0;
    int mismatches = 0;
    for (; checked < npc_count; ++checked) {
        long long key;
        int expected_hp, source_en, source_mp, expected_side;
        if (fscanf(expected, "%lld %d %d %d %d", &key, &expected_hp, &source_en, &source_mp, &expected_side) != 5) {
            break;
        }
        void *id_args[] = {&key};
        void *id = construct(npc_id_class, 1, id_args);
        void *lookup_args[] = {id};
        void *unit = 0;
        if (!id || !call(repository_class, "FindByNpcId", 1, repository, lookup_args, &unit) || !unit) {
            printf("REUSE npc_missing %lld\n", key);
            break;
        }
        void *unit_class = object_class(unit);
        void *unique_id = 0;
        void *identity = 0;
        void *hp = 0, *en = 0, *mp = 0, *side = 0;
        if (!call(unit_class, "get_Id", 0, unit, 0, &identity) || !identity ||
            !call(unit_id_class, "ToInt", 0, identity, 0, &unique_id) || !unique_id ||
            !call(unit_class, "get_CurrentHp", 0, unit, 0, &hp) || !hp ||
            !call(unit_class, "get_CurrentEn", 0, unit, 0, &en) || !en ||
            !call(unit_class, "GetCurrentMp", 0, unit, 0, &mp) || !mp ||
            !call(unit_class, "get_BattleSideType", 0, unit, 0, &side) || !side) {
            break;
        }
        int local_id = *(int *) unbox(unique_id);
        int actual_hp = *(int *) unbox(hp);
        int actual_en = *(int *) unbox(en);
        int actual_mp = *(int *) unbox(mp);
        int actual_side = *(int *) unbox(side);
        printf("REUSE npc_unit %lld id %d hp %d en %d mp %d side %d\n",
               key, local_id, actual_hp, actual_en, actual_mp, actual_side);
        for (int previous = 0; previous < checked; ++previous) {
            if (seen[previous] == local_id) {
                ++mismatches;
            }
        }
        seen[checked] = local_id;
        /* UserUnitStatus clamps EN to at least 1. The serialized NPC pilot has no MP field,
           so IngameUnitEntity starts with 0 MP before the opening effects are applied. */
        int expected_en = source_en < 1 ? 1 : source_en;
        int expected_mp = 0;
        if (actual_hp != expected_hp || actual_en != expected_en || actual_mp != expected_mp ||
            actual_side != expected_side) {
            printf("REUSE npc_expected %lld hp %d en %d mp %d side %d source_en %d source_mp %d\n",
                   key, expected_hp, expected_en, expected_mp, expected_side, source_en, source_mp);
            ++mismatches;
        }
    }
    fclose(expected);
    printf("REUSE native_npc_roster %d mismatches %d\n", checked, mismatches);
    return checked == npc_count && !mismatches;
}

struct player_slot {
    int force;
    int position;
};

static int uses_custom_formation(void) {
    void *marker = fopen(battle_path("stage/CustomFormation.enabled"), "r");
    if (!marker) {
        return 0;
    }
    fclose(marker);
    return 1;
}

static void log_player_levels(void *(*object_class)(void *), void *(*unbox)(void *),
                              void *unit, void *player, int force, int position) {
    void *level = 0, *pilot_level = 0, *ssp = 0, *ssp_level = 0, *step = 0, *stars = 0;
    void *klass = object_class(unit);
    if (call(klass, "get_UnitCurrentLevel", 0, unit, 0, &level) && level &&
        call(klass, "get_CharacterCurrentLevel", 0, unit, 0, &pilot_level) && pilot_level &&
        call(klass, "get_LimitBreakStep", 0, unit, 0, &step) && step &&
        call(object_class(step), "get_Value", 0, step, 0, &stars) && stars) {
        if (call(object_class(player), "get_UserUnitSspEntity", 0, player, 0, &ssp) && ssp) {
            call(object_class(ssp), "get_Level", 0, ssp, 0, &ssp_level);
        }
        printf("REUSE custom_player_levels force %d slot %d unit %d pilot %d stars %d ssp %d\n",
               force, position, *(short *) unbox(level), *(short *) unbox(pilot_level), *(short *) unbox(stars),
               ssp_level ? *(short *) unbox(ssp_level) : 0);
    }
}

static int check_player_units(void *library, const struct battle_images *images, void *repository,
                              const struct deployment *deployment, void *master_stage,
                              struct player_slot *slots, int custom_formation) {
    struct expected_player {
        int force;
        int position;
        long long unit;
        long long character;
        long long master;
        int seen;
    } expected[FIXTURE_PLAYER_UNITS] = {0};
    void *file = fopen(battle_path("stage/PlayerExpected.tsv"), "r");
    if (!file) {
        return 0;
    }
    int lines = 0;
    for (; lines < FIXTURE_PLAYER_UNITS; ++lines) {
        struct expected_player *row = &expected[lines];
        int fields = custom_formation
            ? fscanf(file, "%d %d %lld %lld %lld", &row->force, &row->position, &row->unit, &row->character, &row->master)
            : fscanf(file, "%d %d %lld %lld", &row->force, &row->position, &row->unit, &row->character);
        if (fields != (custom_formation ? 5 : 4)) {
            break;
        }
    }
    fclose(file);
    if (lines != FIXTURE_PLAYER_UNITS) {
        return 0;
    }
    void *(*unbox)(void *) = dlsym(library, "il2cpp_object_unbox");
    void *(*object_class)(void *) = dlsym(library, "il2cpp_object_get_class");
    if (!unbox || !object_class) {
        return 0;
    }
    void *formation = deployment->formation;
    void *formation_class = object_class(formation);
    void *force_args[] = {master_stage};
    void *own_force = 0, *guest_force = 0;
    void *own_group = 0, *guest_group = 0;
    if (!call(formation_class, "GetOwnGroupFormationForceType", 1, formation, force_args, &own_force) || !own_force ||
        !call(formation_class, "GetGuestGroupFormationForceType", 1, formation, force_args, &guest_force) || !guest_force ||
        !call(formation_class, "GetFreeIngameUnitGroup", 1, formation, force_args, &own_group) || !own_group ||
        !call(formation_class, "GetSeriesIngameUnitGroup", 1, formation, force_args, &guest_group) || !guest_group) {
        return 0;
    }
    int own_force_index = *(int *) unbox(own_force);
    int guest_force_index = *(int *) unbox(guest_force);
    printf("REUSE player_formation_forces own %d guest %d\n", own_force_index, guest_force_index);
    void *repository_class = object_class(repository);
    for (int local_id = 1; local_id <= FIXTURE_PLAYER_UNITS; ++local_id) {
        void *id_args[] = {&local_id};
        void *id = new_domain(images, "IngameUnitId", 1, id_args);
        void *lookup_args[] = {id};
        void *unit = 0;
        if (!id || !call(repository_class, "FindById", 1, repository, lookup_args, &unit) || !unit) {
            return 0;
        }
        void *klass = object_class(unit);
        void *player = 0, *pilot = 0, *group = 0;
        void *player_id = 0, *user_unit_id = 0, *character_id = 0, *slot = 0, *side = 0;
        if (!call(klass, "get_UserUnit", 0, unit, 0, &player) || !player ||
            !call(klass, "get_UserCharacter", 0, unit, 0, &pilot) || !pilot ||
            !call(object_class(player), "get_Id", 0, player, 0, &player_id) || !player_id ||
            !call(object_class(player_id), "ToLong", 0, player_id, 0, &user_unit_id) || !user_unit_id ||
            !call(object_class(pilot), "get_Id", 0, pilot, 0, &character_id) || !character_id ||
            !call(klass, "get_GroupFormationPositionNumber", 0, unit, 0, &slot) || !slot ||
            !call(klass, "get_IngameUnitGroup", 0, unit, 0, &group) || !group ||
            !call(klass, "get_BattleSideType", 0, unit, 0, &side) || !side) {
            return 0;
        }
        long long unit_key = *(long long *) unbox(user_unit_id);
        long long pilot_key = *(long long *) unbox(character_id);
        int position = *(int *) unbox(slot);
        int faction = *(int *) unbox(side);
        int match = 0;
        for (int i = 0; i < lines; ++i) {
            struct expected_player *row = &expected[i];
            if (!row->seen && row->unit == unit_key && row->character == pilot_key &&
                row->position == position) {
                void *source_group = row->force == own_force_index ? own_group
                    : row->force == guest_force_index ? guest_group : 0;
                void *group_args[] = {source_group};
                void *same = 0;
                if (source_group && call(object_class(group), "IsSameGroup", 1, group, group_args, &same) &&
                    same && *(unsigned char *) unbox(same)) {
                    if (custom_formation) {
                        void *master = 0, *id = 0;
                        if (!call(object_class(player), "get_MasterUnit", 0, player, 0, &master) || !master ||
                            !call(object_class(master), "Eternal.Domain.ISortableId.get_SortableId", 0, master, 0, &id) ||
                            !id) {
                            return 0;
                        }
                        long long actual_master = *(long long *) unbox(id);
                        if (actual_master != row->master) {
                            printf("REUSE sortie_form_mismatch expected %lld actual %lld\n", row->master, actual_master);
                            printf("UI\tSORTIE_INVALID\t%d\t%d\n", row->force + 1, position);
                            return 0;
                        }
                        log_player_levels(object_class, unbox, unit, player, row->force + 1, position);
                    }
                    row->seen = 1;
                    match = 1;
                    slots[local_id - 1] = (struct player_slot) {row->force + 1, position};
                }
                break;
            }
        }
        printf("REUSE player_unit id %d side %d slot %d matched %d\n", local_id, faction, position, match);
        if (!match) {
            return 0;
        }
    }
    void *supporters = 0, *count = 0;
    void *supporter_repository = deployment->supporter_repository;
    if (!call(object_class(supporter_repository), "FindAll", 0, supporter_repository, 0, &supporters) ||
        !supporters || !call(object_class(supporters), "get_Count", 0, supporters, 0, &count) || !count) {
        return 0;
    }
    int supporter_count = *(int *) unbox(count);
    if (custom_formation) {
        for (int force = 0; force < 2; ++force) {
            void *args[] = {&force};
            void *supporter = 0, *level = 0, *master = 0, *id = 0, *key = 0;
            if (call(formation_class, "GetMainUserSupporter", 1, formation, args, &supporter) && supporter &&
                call(object_class(supporter), "get_Level", 0, supporter, 0, &level) && level &&
                call(object_class(supporter), "get_MasterSupporter", 0, supporter, 0, &master) && master &&
                call(object_class(master), "get_Id", 0, master, 0, &id) && id &&
                call(object_class(id), "ToLong", 0, id, 0, &key) && key) {
                printf("REUSE custom_supporter_level force %d master %lld level %d\n", force + 1,
                       *(long long *) unbox(key), *(short *) unbox(level));
            }
        }
    }
    printf("REUSE native_player_roster %d supporters %d\n", FIXTURE_PLAYER_UNITS, supporter_count);
    return supporter_count == 2;
}

static void *create_stage_initializer(void *library, const struct battle_images *images,
                                      const struct battle_session *session, void *master_repository) {
    void *(*new_object)(void *) = dlsym(library, "il2cpp_object_new");
    void *(*get_field)(void *, const char *) = dlsym(library, "il2cpp_class_get_field_from_name");
    void (*set_object_field)(void *, void *, void *) = dlsym(library, "il2cpp_field_set_value_object");
    if (!new_object || !get_field || !set_object_field) {
        return 0;
    }
    void *initializer_class = get_class(images->application, "Eternal.Application", "IngameInitializer");
    void *initializer = initializer_class ? new_object(initializer_class) : 0;
    void *repository_field = initializer_class ? get_field(initializer_class, "_unitRepository") : 0;
    void *master_field = initializer_class ? get_field(initializer_class, "MasterObjectRepository") : 0;
    void *random_field = initializer_class ? get_field(initializer_class, "_stageRandomRepository") : 0;
    if (!initializer || !repository_field || !master_field || !random_field) {
        return 0;
    }
    /* The native map builder reads these repositories. The full initializer
       constructor also creates the UI sound use case through its view factory. */
    set_object_field(initializer, repository_field, session->unit_repository);
    set_object_field(initializer, master_field, master_repository);
    set_object_field(initializer, random_field, session->random_repository);
    return initializer;
}

static int initialize_map_root(void *library, const struct battle_images *images,
                               const struct battle_session *session, void *scene, void *map,
                               const struct player_slot *slots, int custom_formation) {
    void *(*unbox)(void *) = dlsym(library, "il2cpp_object_unbox");
    void *(*object_class)(void *) = dlsym(library, "il2cpp_object_get_class");
    if (!unbox || !object_class) {
        return 0;
    }
    void *initializer = session->stage_initializer;
    void *initializer_class = object_class(initializer);
    void *scene_args[] = {scene};
    void *root = 0;
    if (!call(initializer_class, "_InitMapRootEntity", 1, initializer, scene_args, &root) || !root) {
        return 0;
    }
    void *map_repository = session->map_repository;
    void *unit_repository = session->unit_repository;
    void *map_repository_class = object_class(map_repository);
    void *save_args[] = {root};
    void *unused = 0, *saved = 0, *current_map = 0, *alive_units = 0, *count = 0, *origins = 0;
    if (!call(map_repository_class, "Save", 1, map_repository, save_args, &unused) ||
        !call(map_repository_class, "FindLatest", 0, map_repository, 0, &saved) || saved != root ||
        !call(object_class(root), "get_CurrentMap", 0, root, 0, &current_map) || current_map != map ||
        !call(object_class(root), "get_UnitMapOriginPlaceDict", 0, root, 0, &origins) || !origins ||
        !call(object_class(unit_repository), "FindAliveOnMap", 0, unit_repository, 0, &alive_units) || !alive_units ||
        !call(object_class(alive_units), "Count", 0, alive_units, 0, &count) || !count) {
        return 0;
    }
    int active = *(int *) unbox(count);
    void *origin_class = object_class(origins);
    int positioned = 0, unavailable_players = 0;
    for (int unit_number = 1; unit_number <= session->unit_count; ++unit_number) {
        void *id_args[] = {&unit_number};
        void *id = new_domain(images, "IngameUnitId", 1, id_args);
        void *lookup_args[] = {id};
        void *place = 0;
        if (!id || !call(origin_class, "FindByUnitId", 1, origins, lookup_args, &place)) {
            return 0;
        }
        positioned += place != 0;
        if (!place && unit_number <= FIXTURE_PLAYER_UNITS) {
            void *unit = 0, *existence = 0;
            if (!call(object_class(unit_repository), "FindById", 1, unit_repository, lookup_args, &unit) || !unit ||
                !call(object_class(unit), "get_ExistenceType", 0, unit, 0, &existence) || !existence) {
                return 0;
            }
            int state = *(int *) unbox(existence);
            printf("REUSE original_player_off_map unit %d existence %d\n", unit_number, state);
            if (custom_formation && state == 9) {
                const struct player_slot *slot = &slots[unit_number - 1];
                printf("UI\tSORTIE_INVALID\t%d\t%d\n", slot->force, slot->position);
            }
            if (state != 9) { /* UnitExistenceType.CannotSortie */
                return 0;
            }
            ++unavailable_players;
        }
    }
    printf("REUSE native_map_root_created active_units %d positioned_units %d\n", active, positioned);
    int expected_active = FIXTURE_PLAYER_UNITS - unavailable_players + session->stage->initial_npcs +
        session->stage->initial_gimmicks;
    return !(custom_formation && unavailable_players) && unavailable_players < FIXTURE_PLAYER_UNITS &&
        active == expected_active && positioned == expected_active;
}

int verify_roster(void *library, const struct battle_images *images, void *unit_repository,
                  void *master_repository, const struct stage_expectations *stage_expected,
                  void *stage_id, void *scene, void *map, int load_player,
                  int load_opening, enum battle_action_mode action) {
    void *(*get_field)(void *, const char *) = dlsym(library, "il2cpp_class_get_field_from_name");
    void (*get_static_field)(void *, void *) = dlsym(library, "il2cpp_field_static_get_value");
    void *(*object_class)(void *) = dlsym(library, "il2cpp_object_get_class");
    void *(*array_new)(void *, unsigned long) = dlsym(library, "il2cpp_array_new");
    void *(*unbox)(void *) = dlsym(library, "il2cpp_object_unbox");
    if (!get_field || !get_static_field || !object_class || !array_new || !unbox || !images->application) {
        return 14;
    }
    void *stage_args[] = {stage_id};
    void *master_stage = 0;
    void *terrain = 0;
    if (!call(object_class(master_repository), "GetMasterStage", 1, master_repository, stage_args, &master_stage) ||
        !master_stage || !call(object_class(master_stage), "get_StageTerrainType", 0, master_stage, 0, &terrain) || !terrain) {
        return 14;
    }
    int terrain_type = *(int *) unbox(terrain);
    void *challenge_class = get_class(images->domain, "Eternal.Domain", "MainStageChallengeId");
    void *challenge_none = challenge_class ? get_field(challenge_class, "NONE") : 0;
    void *reinforcement_class = get_class(images->domain, "Eternal.Domain", "StageReinforcementNpc");
    void *reinforcements = reinforcement_class ? array_new(reinforcement_class, 0) : 0;
    if (!challenge_none || !reinforcements) {
        return 14;
    }
    long long challenge_id = 0;
    get_static_field(challenge_none, &challenge_id);

    printf("REUSE npc_stage_terrain %d\n", terrain_type);
    fflush(0);
    struct player_selection selection = {0};
    if (load_player) {
    void *file = fopen(battle_path("stage/PlayerSelection.tsv"), "r");
        if (!file) {
            return 15;
        }
        int fields = fscanf(file, "%lld %d %lld", &selection.user_id, &selection.formation_type, &selection.content_target);
        fclose(file);
        if (fields != 3 || selection.user_id <= 0 || selection.content_target <= 0) {
            return 15;
        }
    }
    void *store = initialize_user_store(library, images, load_player);
    if (!store) {
        return 15;
    }
    printf("REUSE npc_user_store_ready\n");
    fflush(0);
    struct deployment deployment = create_initializer(library, images, unit_repository, master_repository, store,
                                                       load_player ? &selection : 0);
    if (!deployment.initializer) {
        return 15;
    }
    deployment.session.stage = stage_expected;
    deployment.session.stage_terrain = terrain_type;
    deployment.session.unit_count = stage_expected->npc_count + stage_expected->gimmick_count +
        (load_player ? FIXTURE_PLAYER_UNITS : 0);
    if (load_player) {
        deployment.session.stage_initializer = create_stage_initializer(library, images, &deployment.session,
                                                                        master_repository);
        if (!deployment.session.stage_initializer) {
            return 18;
        }
    }
    if (load_opening && !initialize_battle_random(library, images, &deployment.session)) {
        return 19;
    }
    void *unused = 0;
    void *initializer_class = object_class(deployment.initializer);
    struct player_slot player_slots[FIXTURE_PLAYER_UNITS] = {0};
    const int custom_formation = load_player && uses_custom_formation();
    if (load_player) {
        void *player_args[] = {deployment.formation, scene, map, &terrain_type, &challenge_id};
        printf("REUSE player_setup_enter\n");
        fflush(0);
        if (!call(initializer_class, "_SetupPlayerSideData", 5, deployment.initializer, player_args, &unused)) {
            return 16;
        }
        printf("REUSE player_setup_return\n");
        if (!check_player_units(library, images, unit_repository, &deployment, master_stage,
                                player_slots, custom_formation)) {
            return 17;
        }
    }
    void *npc_args[] = {scene, map, &terrain_type, reinforcements, &challenge_id};
    printf("REUSE npc_setup_enter\n");
    fflush(0);
    if (!call(initializer_class, "_SetupNpcData", 5, deployment.initializer, npc_args, &unused)) {
        return 16;
    }
    printf("REUSE npc_setup_return\n");
    fflush(0);
    if (!check_npc_units(library, images, unit_repository, deployment.session.unit_count, stage_expected->npc_count)) {
        return 17;
    }
    const struct battle_stage_args stage = {
        .id = stage_id, .master = master_stage, .scene = scene, .reinforcements = reinforcements,
        .formation_type = selection.formation_type, .formation_target = selection.content_target,
        .challenge_id = challenge_id,
    };
    if (load_opening && !initialize_battle_context(library, images, &deployment.session, &stage)) {
        return 20;
    }
    if (load_player && !initialize_map_root(library, images, &deployment.session, scene, map,
                                          player_slots, custom_formation)) {
        return 18;
    }
    if (load_opening && !open_battle_turn(library, images, &deployment.session)) {
        return 21;
    }
    if (action == BATTLE_NO_ACTION || run_battle_action(library, images, &deployment.session, action)) {
        return 0;
    }
    return action == BATTLE_STRIKES ? 22 : 23;
}
