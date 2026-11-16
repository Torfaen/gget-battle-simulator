/* Connect the original async attack use case to the independent stage session. */

#include "android_interactive.h"

extern void *dlsym(void *library, const char *name);
extern int printf(const char *format, ...);
extern int fflush(void *stream);
extern void *fopen(const char *path, const char *mode);
extern int fclose(void *file);

static void *application(const struct battle_images *images, const char *name, int arity, void **args) {
    return construct(get_class(images->application, "Eternal.Application", name), arity, args);
}

static void *domain(const struct battle_images *images, const char *name, int arity, void **args) {
    return construct(get_class(images->domain, "Eternal.Domain", name), arity, args);
}

static void *infrastructure(const struct battle_images *images, const char *name, int arity, void **args) {
    return construct(get_class(images->infrastructure, "Eternal.Infrastructure", name), arity, args);
}

static void *create_headless_user_config(void *library, const struct battle_images *images,
                                        const struct battle_session *session, void *save) {
    void *(*object_class)(void *) = dlsym(library, "il2cpp_object_get_class");
    void *(*unbox)(void *) = dlsym(library, "il2cpp_object_unbox");
    void *(*get_field)(void *, const char *) = dlsym(library, "il2cpp_class_get_field_from_name");
    void (*get_static_field)(void *, void *) = dlsym(library, "il2cpp_field_static_get_value");
    void (*set_field)(void *, void *, void *) = dlsym(library, "il2cpp_field_set_value_object");
    if (!object_class || !unbox || !get_field || !get_static_field || !set_field) {
        return 0;
    }
    void *defaults = application(images, "ReplayUserConfigRepository", battle_client_28 ? 1 : 0, &save);
    void *user_config = 0, *ingame_config = 0, *unused = 0;
    if (!defaults || !call(object_class(defaults), "Load", 0, defaults, 0, &user_config) || !user_config ||
        !call(object_class(user_config), "get_IngameConfig", 0, user_config, 0, &ingame_config) || !ingame_config) {
        return 0;
    }
    void *mode_class = get_class(images->domain, "Eternal.Domain", "IngameAutoMode");
    void *mode_field = mode_class ? get_field(mode_class, "OtherPhaseAuto") : 0;
    if (!mode_field) {
        return 0;
    }
    int mode = 0;
    get_static_field(mode_field, &mode);
    void *mode_args[] = {&mode};
    void *ingame_class = object_class(ingame_config);
    if (!call(ingame_class, "ReleaseForceDisableAutoMode", 0, ingame_config, 0, &unused) ||
        !call(ingame_class, "SetAutoMode", 1, ingame_config, mode_args, &unused)) {
        return 0;
    }

    void *repository_args[] = {session->user_data_store, save, 0};
    void *repository = infrastructure(images, "UserConfigRepository", 3, repository_args);
    void *field = repository ? get_field(object_class(repository), "_userConfig") : 0;
    if (!field) {
        return 0;
    }
    set_field(repository, field, user_config);
    void *installed = 0, *enemy_auto = 0, *own_auto = 0;
    int enemy = 2, own = 0;
    void *enemy_args[] = {&enemy}, *own_args[] = {&own};
    if (!call(object_class(repository), "LoadIngameConfig", 0, repository, 0, &installed) || installed != ingame_config ||
        !call(ingame_class, "IsAuto", 1, installed, enemy_args, &enemy_auto) || !enemy_auto ||
        !call(ingame_class, "IsAuto", 1, installed, own_args, &own_auto) || !own_auto ||
        !*(unsigned char *) unbox(enemy_auto) || *(unsigned char *) unbox(own_auto)) {
        return 0;
    }
    printf("REUSE original_config_other_phase_auto\n");
    return repository;
}

static void *create_local_user_application(void *library, const struct battle_images *images,
                                           const struct battle_session *session,
                                           const struct battle_exchange *exchange) {
    void *(*new_object)(void *) = dlsym(library, "il2cpp_object_new");
    void *(*get_field)(void *, const char *) = dlsym(library, "il2cpp_class_get_field_from_name");
    void (*set_field)(void *, void *, void *) = dlsym(library, "il2cpp_field_set_value_object");
    void *klass = get_class(images->application, "Eternal.Application", "UserApplicationService");
    if (!klass || !new_object || !get_field || !set_field) {
        return 0;
    }
    /* The production constructor also initializes the purchase service.
       Set the same local account dependencies for the battle use cases. */
    void *service = new_object(klass);
    if (!service) {
        return 0;
    }
    const struct {
        const char *name;
        void *value;
    } fields[] = {
        {"_dateTimeService", session->date_time},
        {"_saveDataService", exchange->save},
        {"_masterObjectRepository", session->master_repository},
        {"_userConfigRepository", exchange->config},
        {"_userGroupFormationRepository", session->user_group_formation_repository},
        {"_userInfoRepository", session->user_info_repository},
        {"_optionPartsRepository", session->option_parts_repository},
        {"_userSupporterRepository", session->user_supporter_repository},
        {"_userUnitRepository", session->user_unit_repository},
        {"_userRecordRepository", session->user_record_repository},
        {"_userCharacterRepository", session->user_character_repository},
    };
    for (unsigned int i = 0; i < sizeof(fields) / sizeof(fields[0]); ++i) {
        void *field = get_field(klass, fields[i].name);
        if (!field || !fields[i].value) {
            return 0;
        }
        set_field(service, field, fields[i].value);
    }
    return service;
}

int initialize_exchange(void *library, const struct battle_images *images, const struct battle_session *session,
                        struct battle_exchange *exchange) {
    /* The 2.8.0 test guest supplies a headless asset view. Its battle data comes
       from the stage bundle and master tables loaded by verify_master. */
    if (!session->view || !bind_headless_presentation(library, images) ||
        (!battle_client_28 && !initialize_octo_catalog(library, images->octo, session->stage->resource))) {
        return 0;
    }
    exchange->view = session->view;
    exchange->adv = application(images, "MasterLeagueAdvApplicationService", 0, 0);
    exchange->score_repository = infrastructure(images, "InMemoryBattleScoreRepository", 0, 0);
    exchange->score_calculator = infrastructure(images, "InMemoryBattleScoreCalculatorRepository", 0, 0);
    exchange->adv_repository = infrastructure(images, "ADVContextRepository", 0, 0);
    void *save_args[] = {0};
    exchange->save = infrastructure(images, "EternalLocalSaveDataService", 1, save_args);
    exchange->config = exchange->save ? create_headless_user_config(library, images, session, exchange->save) : 0;
    if (!exchange->view || !exchange->config || !exchange->adv ||
        !exchange->score_repository || !exchange->score_calculator || !exchange->adv_repository || !exchange->save) {
        return 0;
    }
    printf("REUSE original_no_presentation_view_ready\n");
    fflush(0);

    void *units = session->unit_repository;
    void *master = session->master_repository;
    void *turns = session->turn_repository;
    void *context = session->context_repository;
    void *map = session->map_repository;
    void *info = session->user_info_repository;
    void *traits = session->trait_service;
    void *config_args[] = {exchange->view, turns, exchange->config, context};
    exchange->config_service = application(images, "IngameConfigService", 4, config_args);
    exchange->user_application = create_local_user_application(library, images, session, exchange);
    if (!exchange->config_service || !exchange->user_application) {
        return 0;
    }
    void *name_args[] = {context, master};
    void *tower_name_args[] = {context, master, info, exchange->user_application};
    void *stage_name = session->stage->tower_stage_id
        ? application(images, "GenerationTowerStageNameCreator", 4, tower_name_args)
        : application(images, "IngameStageNameCreator", 2, name_args);
    if (!stage_name) {
        return 0;
    }
    void *condition_args[] = {
        exchange->adv, exchange->config_service, exchange->view, context, units, map, turns, session->supporter_repository,
        exchange->config, master, info, exchange->user_application, 0, stage_name, 0, 0, session->date_time,
    };
    exchange->conditions = application(images, "IngameBattleConditionService", 17, condition_args);
    void *koma_args[] = {traits, exchange->view, units};
    exchange->koma = application(images, "IngameKomaService", 3, koma_args);
    void *score_args[] = {master, units, exchange->score_calculator, exchange->score_repository};
    exchange->score = domain(images, "BattleScoreService", 4, score_args);
    if (!exchange->conditions || !exchange->koma || !exchange->score) {
        return 0;
    }
    void *formatter_args[] = {master, map, context, traits, session->date_time, info};
    if (!infrastructure(images, "IngameMemoryPackFormatterRegister", 6, formatter_args)) {
        return 0;
    }
    printf("REUSE original_battle_snapshot_formatters_ready\n");
    fflush(0);
    void *memento_args[] = {
        exchange->view, exchange->adv_repository, exchange->score_repository, exchange->save,
        context, session->supporter_repository,
        session->trait_repository, units, map, master, exchange->conditions, traits, turns,
        session->user_unit_repository, info, exchange->koma, session->aura_service,
    };
    exchange->memento = application(images, "IngameMementoManager", 17, memento_args);
    return exchange->memento != 0;
}

static void *create_attack_use_case(const struct battle_images *images, const struct battle_session *session,
                                    const struct battle_exchange *exchange, const struct combat_services *combat,
                                    struct attack_services *shared) {
    void *units = session->unit_repository;
    void *master = session->master_repository;
    void *map = session->map_repository;
    void *turns = session->turn_repository;
    void *context = session->context_repository;
    void *info = session->user_info_repository;
    void *traits = session->trait_service;
    void *view = exchange->view;
    void *config = exchange->config;
    void *situation = application(images, "BattleSituationService", 0, 0);
    void *event_score = application(images, "BattleEventScoreService", 0, 0);
    void *order = application(images, "UnitOrderChecker", 0, 0);
    void *commentary = application(images, "LiveCommentaryViewService", 0, 0);
    void *capture_args[] = {combat->kill_log};
    void *capture = domain(images, "IngameUnitCaptureService", 1, capture_args);
    void *bgm_args[] = {master, context, turns, info};
    void *bgm = domain(images, "IngameBgmService", 4, bgm_args);
    void *buff_args[] = {view, units, traits};
    void *buff_view = application(images, "BuffDebuffViewService", 3, buff_args);
    void *movie_args[] = {config, turns, context};
    void *movie = domain(images, "BattleMovieEnableChecker", 3, movie_args);
    void *movement_args[] = {units, map, turns};
    void *movement = domain(images, "MoveStepCounter", 3, movement_args);
    void *story_repository = infrastructure(images, "InMemoryStoryEventBonusPointRepository", 0, 0);
    void *story_bonus_args[] = {master, context, units, story_repository};
    void *story_bonus = story_repository ? application(images, "IngameStoryEventBonusService", 4, story_bonus_args) : 0;
    void *story_args[] = {view, context, master, story_bonus};
    void *story_points = application(images, "StoryEventBonusTotalPointDisplayService", 4, story_args);
    if (!situation || !event_score || !order || !commentary || !capture || !bgm || !buff_view ||
        !movie || !movement || !story_bonus || !story_points) {
        return 0;
    }
    void *adv_args[] = {
        view, exchange->adv, units, exchange->conditions, capture, turns, config, event_score, story_points, context,
    };
    void *attack_adv = application(images, "AttackTriggeredAdvPlayer", 10, adv_args);
    void *form_args[] = {session->user_unit_repository, master, map, traits};
    void *form_change = application(images, "IngameFormChangeExecutor", 4, form_args);
    void *escape_args[] = {
        view, units, map, master, traits, context, session->user_unit_repository, exchange->koma,
        exchange->conditions, combat->can_escape, form_change, combat->memento,
    };
    void *escape = application(images, "IngameEscapeService", 12, escape_args);
    void *skip_args[] = {view, 0, order, 0, situation, event_score, commentary, 0, 0, units};
    void *skip_view = application(images, "MasterLeagueSkipViewChanger", 10, skip_args);
    if (!attack_adv || !form_change || !escape || !skip_view) {
        return 0;
    }
    *shared = (struct attack_services) {
        .attack_adv = attack_adv, .buff_view = buff_view, .capture = capture, .situation = situation,
        .event_score = event_score, .order = order, .live_commentary = commentary, .movie = movie,
        .escape = escape, .form_change = form_change, .story_points = story_points, .movement = movement,
    };
    void *arguments[] = {
        view, combat->attack, attack_adv, exchange->score, combat->calculator, context, combat->memento, units,
        map, master, info, exchange->conditions, traits, turns, config, combat->kill_log, buff_view, situation,
        exchange->adv, bgm, event_score, order, movie, escape, story_points, movement, session->date_time,
        commentary, skip_view,
    };
    return application(images, "AttackUseCase", 29, arguments);
}

enum { LOOP_TIMINGS = 16, TASK_FRAME_LIMIT = 2400 };

struct player_loop {
    void *queue_class;
    void *runner_class;
    void *queues[LOOP_TIMINGS];
    void *runners[LOOP_TIMINGS];
};

static int get(const struct exchange_api *api, void *object, const char *method, void **value) {
    return object && call(api->object_class(object), method, 0, object, 0, value) && *value;
}

static int initialize_player_loop(void *library, const struct battle_images *images, const void *corlib,
                                  const struct exchange_api *api, struct player_loop *loop) {
    void *(*get_field)(void *, const char *) = dlsym(library, "il2cpp_class_get_field_from_name");
    void (*set_static_field)(void *, void *) = dlsym(library, "il2cpp_field_static_set_value");
    void (*get_static_field)(void *, void *) = dlsym(library, "il2cpp_field_static_get_value");
    void (*class_init)(void *) = dlsym(library, "il2cpp_runtime_class_init");
    void *(*array_new)(void *, unsigned long) = dlsym(library, "il2cpp_array_new");
    unsigned int (*array_header_size)(void) = dlsym(library, "il2cpp_array_object_header_size");
    void (*write_barrier)(void *, void **, void *) = dlsym(library, "il2cpp_gc_wbarrier_set_field");
    if (!images->unitask || !get_field || !set_static_field || !get_static_field || !class_init ||
        !array_new || !array_header_size || !write_barrier) {
        return 0;
    }
    void *helper = get_class(images->unitask, "Cysharp.Threading.Tasks", "PlayerLoopHelper");
    loop->queue_class = get_class(images->unitask, "Cysharp.Threading.Tasks.Internal", "ContinuationQueue");
    loop->runner_class = get_class(images->unitask, "Cysharp.Threading.Tasks.Internal", "PlayerLoopRunner");
    if (!helper || !loop->queue_class || !loop->runner_class) {
        return 0;
    }
    class_init(helper);
    void *yielders_field = get_field(helper, "yielders");
    void *runners_field = get_field(helper, "runners");
    void *thread_field = get_field(helper, "mainThreadId");
    void *yielders = array_new(loop->queue_class, LOOP_TIMINGS);
    void *runners = array_new(loop->runner_class, LOOP_TIMINGS);
    void *thread_class = get_class(corlib, "System.Threading", "Thread");
    void *thread = 0, *thread_id = 0;
    if (!yielders_field || !runners_field || !thread_field || !yielders || !runners ||
        !call(thread_class, "get_CurrentThread", 0, 0, 0, &thread) ||
        !get(api, thread, "get_ManagedThreadId", &thread_id) || !bind_player_loop_clock(library)) {
        return 0;
    }
    void **yielder_items = (void **) ((char *) yielders + array_header_size());
    void **runner_items = (void **) ((char *) runners + array_header_size());
    for (int timing = 0; timing < LOOP_TIMINGS; ++timing) {
        void *args[] = {&timing};
        loop->queues[timing] = construct(loop->queue_class, 1, args);
        loop->runners[timing] = construct(loop->runner_class, 1, args);
        if (!loop->queues[timing] || !loop->runners[timing]) {
            return 0;
        }
        write_barrier(yielders, &yielder_items[timing], loop->queues[timing]);
        write_barrier(runners, &runner_items[timing], loop->runners[timing]);
    }
    set_static_field(thread_field, api->unbox(thread_id));
    set_static_field(yielders_field, yielders);
    set_static_field(runners_field, runners);
    void *installed_yielders = 0, *installed_runners = 0;
    get_static_field(yielders_field, &installed_yielders);
    get_static_field(runners_field, &installed_runners);
    if (installed_yielders != yielders || installed_runners != runners) {
        printf("REUSE player_loop_array_install_failed\n");
        return 0;
    }
    printf("REUSE original_player_loop_ready timings %d\n", LOOP_TIMINGS);
    fflush(0);
    return 1;
}

static int tick_player_loop(const struct player_loop *loop) {
    if (!advance_player_loop_clock()) {
        return 0;
    }
    for (int timing = 0; timing < LOOP_TIMINGS; ++timing) {
        void *unused = 0;
        if (!call(loop->queue_class, "Run", 0, loop->queues[timing], 0, &unused) ||
            !call(loop->runner_class, "Run", 0, loop->runners[timing], 0, &unused)) {
            return 0;
        }
    }
    return 1;
}

int finish_task(const struct exchange_api *api, const struct player_loop *loop, const char *name,
                       void *task, void **result, void *state_machine) {
    void *awaiter = 0, *completed = 0, *unused = 0;
    if (!task || !call(api->object_class(task), "GetAwaiter", 0, api->unbox(task), 0, &awaiter) || !awaiter) {
        return 0;
    }
    void *klass = api->object_class(awaiter), *value = api->unbox(awaiter);
    void *last_state_class = 0;
    for (int frame = 0; frame <= TASK_FRAME_LIMIT; ++frame) {
        if (unity_exception_logged() || !call(klass, "get_IsCompleted", 0, value, 0, &completed) || !completed) {
            return 0;
        }
        if (*(unsigned char *) api->unbox(completed)) {
            if (!call(klass, "GetResult", 0, value, 0, result ? result : &unused)) {
                return 0;
            }
            printf("REUSE original_task_completed %s frames %d\n", name, frame);
            return stage_event_playback_ok();
        }
        if (state_machine && frame % 30 == 0) {
            void *state = 0;
            if (!call(api->object_class(state_machine), "GetCurrentState", 0, state_machine, 0, &state)) {
                return 0;
            }
            void *state_class = state ? api->object_class(state) : 0;
            if (state_class && state_class != last_state_class) {
                printf("REUSE original_phase_state %s frame %d\n", api->class_name(state_class), frame);
                fflush(0);
                last_state_class = state_class;
            }
        }
        if (frame == TASK_FRAME_LIMIT || !tick_player_loop(loop)) {
            break;
        }
    }
    printf("REUSE original_task_pending %s\n", name);
    return 0;
}

static int unit_resources(const struct exchange_api *api, void *unit, int values[3]) {
    const char *methods[] = {"get_CurrentHp", "get_CurrentEn", "GetCurrentMp"};
    for (int i = 0; i < 3; ++i) {
        void *value = 0;
        if (!get(api, unit, methods[i], &value)) {
            return 0;
        }
        values[i] = *(int *) api->unbox(value);
    }
    return 1;
}

static int verify_exchange_settlement(const struct battle_session *session, const struct battle_exchange *exchange,
                                      const struct exchange_api *api, void *target, int target_hp) {
    void *snapshot = 0;
    if (!get(api, exchange->memento, "get_LatestStep", &snapshot)) {
        return 0;
    }
    if (target_hp > 0) {
        printf("REUSE original_exchange_snapshot_saved\n");
        return 1;
    }
    void *context = 0, *kill_log = 0, *id = 0, *map = 0, *origins = 0;
    if (!get(api, target, "get_Id", &id) ||
        !get(api, session->context_repository, "Get", &context) ||
        !get(api, context, "get_KillLogList", &kill_log) ||
        !get(api, session->map_repository, "FindLatest", &map) ||
        !get(api, map, "get_UnitMapOriginPlaceDict", &origins)) {
        return 0;
    }
    void *args[] = {id};
    void *entry = 0, *origin = 0;
    if (!call(api->object_class(kill_log), "GetKillLogByUnitId", 1, kill_log, args, &entry) ||
        !call(api->object_class(origins), "FindByUnitId", 1, origins, args, &origin)) {
        return 0;
    }
    printf("REUSE original_exchange_snapshot_saved kill_recorded %d removed_from_map %d\n",
           entry != 0, origin == 0);
    fflush(0);
    return entry && !origin;
}

static int change_to_next_phase(const struct exchange_api *api, void *core, int *turn_number, int *battle_side) {
    void *turn = 0, *number = 0, *side = 0;
    if (!call(api->object_class(core), "ChangeToNextPhase", 0, core, 0, &turn) ||
        !get(api, turn, "get_Number", &number) || !get(api, turn, "get_CurrentPhaseBattleSide", &side)) {
        return 0;
    }
    *turn_number = *(int *) api->unbox(number);
    *battle_side = *(int *) api->unbox(side);
    printf("REUSE original_next_phase turn %d side %d\n", *turn_number, *battle_side);
    fflush(0);
    return 1;
}

static int step_unit_number(const struct exchange_api *api, void *step, const char *method, int *number) {
    void *id = 0, *value = 0;
    *number = 0;
    if (!call(api->object_class(step), method, 0, step, 0, &id)) {
        return 0;
    }
    if (id) {
        if (!get(api, id, "ToInt", &value)) {
            return 0;
        }
        *number = *(int *) api->unbox(value);
    }
    return 1;
}

static int step_coordinate(const struct exchange_api *api, void *step, const char *method, char text[48]) {
    void *coord = 0, *string = 0;
    if (!get(api, step, method, &coord) ||
        !call(api->object_class(coord), "ToString", 0, api->unbox(coord), 0, &string) || !string) {
        return 0;
    }
    const unsigned short *chars = api->string_chars(string);
    int length = api->string_length(string);
    if (!chars || length < 0 || length >= 48) {
        return 0;
    }
    for (int i = 0; i < length; ++i) {
        text[i] = chars[i] >= 32 && chars[i] < 127 ? (char) chars[i] : '?';
    }
    text[length] = 0;
    return 1;
}

static int log_phase_steps(const struct exchange_api *api, const struct battle_session *session,
                           int turn_number, int side, int *actions) {
    void *turn = 0, *phase = 0, *steps = 0, *count = 0, *items = 0;
    if (!get(api, session->turn_repository, "FindLatest", &turn) ||
        !get(api, turn, "GetCurrentPhase", &phase) ||
        !get(api, phase, "GetStepActivityEntityList", &steps) ||
        !get(api, steps, "Count", &count) || !get(api, steps, "Values", &items)) {
        return 0;
    }
    *actions = 0;
    int size = *(int *) api->unbox(count);
    for (int index = 0; index < size; ++index) {
        void *index_args[] = {&index};
        void *step = 0;
        int unit = 0, target = 0;
        char start[48], destination[48];
        if (!call(api->object_class(items), "get_Item", 1, items, index_args, &step) || !step ||
            !step_unit_number(api, step, "get_OperatingIngameUnitId", &unit)) {
            return 0;
        }
        if (unit <= 0) {
            continue;
        }
        if (!step_unit_number(api, step, "get_MainTargetUnitId", &target) ||
            !step_coordinate(api, step, "get_InitUnitCoord", start) ||
            !step_coordinate(api, step, "get_FreeMoveUnitCoord", destination)) {
            return 0;
        }
        printf("REUSE original_phase_step turn %d side %d step %d unit %d target %d from %s to %s\n",
               turn_number, side, index, unit, target, start, destination);
        ++*actions;
    }
    return 1;
}

static int apply_phase_traits(const struct exchange_api *api, const struct battle_session *session,
                              void *on_demand_visuals, int turn, int side) {
    void *units = 0, *values = 0, *visuals = 0;
    if (!get(api, session->unit_repository, "FindAll", &units) || !get(api, units, "Values", &values)) {
        return 0;
    }
    void *args[] = {values};
    if (!call(api->object_class(session->trait_service), "ExecuteOnTurnProgress", 1,
              session->trait_service, args, &visuals)) {
        return 0;
    }
    void *visual_args[] = {visuals};
    void *unused = 0;
    if (visuals && !call(api->object_class(on_demand_visuals), "EnqueueOnPhaseStart", 1,
                         on_demand_visuals, visual_args, &unused)) {
        return 0;
    }
    printf("REUSE original_phase_traits turn %d side %d\n", turn, side);
    return 1;
}

struct phase_unit_status {
    int hp, en, mp, side;
    long long attack_and_counter_damage;
    int present;
};

static int read_phase_units(const struct battle_images *images, const struct battle_session *session,
                            const struct exchange_api *api, struct phase_unit_status *units) {
    void *id_class = get_class(images->domain, "Eternal.Domain", "IngameUnitId");
    void *repository = session->unit_repository;
    for (int number = 1; number <= session->unit_count; ++number) {
        units[number - 1] = (struct phase_unit_status) {0};
        void *id_args[] = {&number};
        void *id = construct(id_class, 1, id_args);
        void *lookup[] = {id};
        void *unit = 0;
        if (!id || !call(api->object_class(repository), "FindById", 1, repository, lookup, &unit)) {
            return 0;
        }
        if (unit) {
            int values[3];
            void *side = 0, *damage = 0;
            if (!unit_resources(api, unit, values) || !get(api, unit, "get_BattleSideType", &side) ||
                !get(api, unit, "get_DamageGivenByAttackAndCounter", &damage)) {
                return 0;
            }
            units[number - 1] = (struct phase_unit_status) {
                values[0], values[1], values[2], *(int *) api->unbox(side), *(long long *) api->unbox(damage), 1,
            };
        }
    }
    return 1;
}

static int log_phase_combat(const struct battle_images *images, const struct battle_session *session,
                            const struct exchange_api *api, int turn, int side,
                            const struct phase_unit_status *before) {
    struct phase_unit_status after[session->unit_count];
    if (!read_phase_units(images, session, api, after)) {
        return 0;
    }
    for (int i = 0; i < session->unit_count; ++i) {
        const struct phase_unit_status *a = &before[i], *b = &after[i];
        if (a->present && !b->present) {
            printf("REUSE original_phase_unit_removed turn %d side %d unit %d\n", turn, side, i + 1);
        } else if (a->present && b->present && (a->hp != b->hp || a->en != b->en || a->mp != b->mp ||
                   a->attack_and_counter_damage != b->attack_and_counter_damage)) {
            printf("REUSE original_phase_combat turn %d side %d unit %d unit_side %d hp %d->%d en %d->%d "
                   "mp %d->%d attack_counter_damage %lld\n", turn, side, i + 1, b->side,
                   a->hp, b->hp, a->en, b->en, a->mp, b->mp, b->attack_and_counter_damage - a->attack_and_counter_damage);
        }
    }
    return 1;
}

static int run_other_side_phase(const struct battle_images *images, const struct battle_session *session,
                                const struct exchange_api *api,
                                const struct player_loop *loop, void *factory, void *back,
                                void *on_demand_visuals, void *token,
                                int turn, int side, int *actions) {
    struct phase_unit_status before[session->unit_count];
    if (!apply_phase_traits(api, session, on_demand_visuals, turn, side) ||
        !read_phase_units(images, session, api, before)) {
        return 0;
    }
    void *phase_args[] = {factory, back};
    void *phase = application(images, "OtherSidePhase", 2, phase_args);
    if (!phase) {
        return 0;
    }
    void *field = api->get_field(api->object_class(phase), "_rootFsm");
    void *state_machine = 0;
    if (field) {
        api->get_field_value(phase, field, &state_machine);
    }
    printf("REUSE original_other_side_phase_enter turn %d side %d\n", turn, side);
    fflush(0);
    void *args[] = {token};
    void *task = 0, *result = 0;
    if (!call(api->object_class(phase), "StartAsync", 1, phase, args, &task)) {
        return 0;
    }
    int completed = finish_task(api, loop, "other_side_phase", task, &result, state_machine);
    if (!completed) {
        printf("REUSE original_phase_partial turn %d side %d\n", turn, side);
    }
    if (!log_phase_steps(api, session, turn, side, actions) ||
        !log_phase_combat(images, session, api, turn, side, before) || !completed || !result) {
        return 0;
    }
    if (*actions) {
        emit_other_phase_board();
    }
    const char *interruptions[] = {"IsGiveUp", "IsRetry"};
    for (unsigned int i = 0; i < sizeof(interruptions) / sizeof(interruptions[0]); ++i) {
        unsigned char interrupted = 0;
        void *flag = api->get_field(api->object_class(result), interruptions[i]);
        if (!flag) {
            return 0;
        }
        api->get_field_value(result, flag, &interrupted);
        if (interrupted) {
            printf("REUSE original_phase_interrupted %s\n", interruptions[i]);
            return 0;
        }
    }
    printf("REUSE original_other_side_phase_return turn %d side %d steps %d\n", turn, side, *actions);
    fflush(0);
    return 1;
}

static int run_phase_task(const struct exchange_api *api, const struct player_loop *loop,
                          const char *label, void *service, const char *method, void *token) {
    void *task = 0;
    void *args[] = {token};
    return service && call(api->object_class(service), method, 1, service, args, &task) &&
        finish_task(api, loop, label, task, 0, 0);
}

static int settle_stage(const struct battle_images *images, const struct battle_session *session,
                        const struct battle_exchange *exchange, const struct stage_phase_services *services,
                        const struct exchange_api *api, void *context, void *core, int turn, int side) {
    void *result = 0, *reason = 0;
    if (!get(api, core, "_FinishMainLoop", &result) || !get(api, context, "GetDefeatReasonType", &reason)) {
        return 0;
    }
    enum { VICTORY = 2, DEFEAT = 3 };
    int outcome = *(int *) api->unbox(result);
    if (outcome != VICTORY && outcome != DEFEAT) {
        return 0;
    }
    printf("REUSE original_stage_outcome %s turn %d side %d defeat_reason %d\n",
           outcome == VICTORY ? "victory" : "defeat", turn, side, *(int *) api->unbox(reason));
    fflush(0);

    void *movie_args[] = {session->master_repository};
    void *movie = application(images, "BattleMovieReadSaveService", 1, movie_args);
    void *stage_args[] = {
        session->master_repository, session->user_data_store, session->user_info_repository,
        session->user_unit_repository, 0, 0, session->date_time, 0, 0,
    };
    void *stage = infrastructure(images, "UserStageRepository", 9, stage_args);
    void *creator_args[] = {
        session->unit_repository, session->context_repository, session->turn_repository,
        session->user_group_formation_repository, session->supporter_repository, exchange->score,
        services->map_score, exchange->user_application, exchange->config, stage, exchange->memento,
        movie, session->master_repository,
    };
    void *creator = movie && stage ? application(images, "IngameStageResultRequestParamCreator", 13, creator_args) : 0;
    const char *create_method = outcome == VICTORY ? "CreateVictoryStageResultApi" : "CreateDefeatStageResultApi";
    void *stage_result = 0, *request = 0, *stage_id = 0, *result_type = 0, *used_turn = 0, *size = 0;
    if (!get(api, creator, create_method, &stage_result) || !get(api, stage_result, "get_RequestParameter", &request) ||
        !get(api, request, "get_StageId", &stage_id) || !get(api, request, "get_StageResultType", &result_type) ||
        !get(api, request, "get_UsedTurn", &used_turn) || !get(api, request, "CalculateSize", &size)) {
        return 0;
    }
    long long id = *(long long *) api->unbox(stage_id), used = *(long long *) api->unbox(used_turn);
    int type = *(int *) api->unbox(result_type), bytes = *(int *) api->unbox(size);
    printf("REUSE original_stage_settlement stage %lld result_type %d used_turn %lld protobuf_bytes %d\n",
           id, type, used, bytes);
    fflush(0);
    return id == session->stage->id && used == turn && type == (outcome == VICTORY ? 1 : 2) && bytes > 0;
}

struct stage_flow {
    struct stage_phase_services phases;
    void *pass;
    void *back;
    void *core;
    void *context;
};

static int initialize_phase_flow(void *library, const struct battle_images *images, const struct battle_session *session,
                                 const struct battle_exchange *exchange, const struct combat_services *combat,
                                 const struct attack_services *attack, const struct exchange_api *api,
                                 enum battle_action_mode action, struct stage_flow *flow) {
    void *turns = session->turn_repository, *units = session->unit_repository;
    void *phase_args[] = {exchange->view, units, turns};
    flow->pass = application(images, "PlayerPhasePassUseCase", 3, phase_args);
    if (action != BATTLE_PHASE) {
        if (!create_stage_phase_services(library, images, session, exchange, combat, attack, &flow->phases)) {
            return 0;
        }
        void *back_args[] = {exchange->view};
        flow->back = application(images, "IngameBackKeyEventHandler", 1, back_args);
        if (!flow->back) {
            return 0;
        }
    }
    if (action == BATTLE_STAGE_RUN || action == BATTLE_INTERACTIVE) {
        void *start_args[] = {
            exchange->view, session->master_repository, units, exchange->conditions, flow->phases.use_cases,
            turns, exchange->config, attack->buff_view, 0,
        };
        flow->phases.own_start = application(images, "OwnPhaseStartUseCase", 9, start_args);
        if (!flow->phases.own_start) {
            return 0;
        }
    }
    void *core_args[] = {
        exchange->view, session->context_repository, units, turns, exchange->conditions, session->trait_service,
        exchange->memento, 0, 0, flow->phases.other_side_factory, flow->back, flow->phases.trait_visuals, attack->buff_view,
    };
    flow->core = application(images, "IngameMainLoopCore", 13, core_args);
    return flow->pass && flow->core && get(api, session->context_repository, "Get", &flow->context);
}

int start_player_phase(const struct battle_session *session, const struct stage_phase_services *phases,
                       const struct exchange_api *api, const struct player_loop *loop, void *token, int turn, int side) {
    return apply_phase_traits(api, session, phases->trait_visuals, turn, side) &&
        run_phase_task(api, loop, "own_phase_start", phases->own_start, "ExecuteAsync", token);
}

static int advance_phase(void *library, const struct battle_images *images, const struct battle_session *session,
                         const struct battle_exchange *exchange, const struct stage_flow *flow,
                         const struct combat_services *combat,
                         const struct exchange_api *api, const struct player_loop *loop,
                         void *token, enum battle_action_mode action) {
    enum { OWN = 0, ENEMY = 2, PHASE_LIMIT = 256 };
    int manual = action == BATTLE_INTERACTIVE;
    int full_stage = action == BATTLE_STAGE_RUN || manual;
    int turn = 1, side = OWN;
    struct battle_history history = {0};
    if (manual) {
        void *timeline_ui = fopen(battle_path("stage/BattleTimeline.enabled"), "rb");
        void *load = fopen(battle_path("stage/BattleTimeline.bin"), "rb");
        void *mumu_checkpoint = battle_client_28 ? fopen(battle_path("stage/MuMuCheckpoint.bin"), "rb") : 0;
        history.keep_open = timeline_ui != 0;
        history.load_pending = load != 0;
        history.mumu_checkpoint_pending = mumu_checkpoint != 0;
        if (timeline_ui) {
            fclose(timeline_ui);
        }
        if (load) {
            fclose(load);
        }
        if (mumu_checkpoint) {
            fclose(mumu_checkpoint);
        }
    }
    int result_code = 0, resumed = 0;
    for (int phase = 0; phase < PHASE_LIMIT; ++phase) {
        void *context = 0, *finished = 0;
        if (!resumed) {
            if (!get(api, session->context_repository, "Get", &context) ||
                !run_phase_task(api, loop, "stage_conditions", exchange->conditions, "CheckAndPlayEventAsync", token) ||
                !get(api, context, "IsBattleFinished", &finished)) {
                break;
            }
            if (*(unsigned char *) api->unbox(finished)) {
                goto settle;
            }
        }

        int actions = 0;
        if (side == OWN) {
            if (!resumed && phase && !start_player_phase(session, &flow->phases, api, loop, token, turn, side)) {
                break;
            }
            resumed = 0;
            enum manual_phase_result result = manual
                ? run_manual_phase(library, images, session, exchange, combat, &flow->phases, api, loop,
                                   token, &turn, side, &history, 0)
                : MANUAL_PASS;
            if (result == MANUAL_QUIT) {
                result_code = 1;
                break;
            }
            if (result == MANUAL_ERROR) {
                break;
            }
            if (manual) {
                phase = 0;
            }
            if (!manual) {
                printf("REUSE scripted_player_pass turn %d side %d\n", turn, side);
            }
            if ((result == MANUAL_PASS && !run_phase_task(api, loop, "player_phase_pass", flow->pass, "ExecuteAsync", token)) ||
                !log_phase_steps(api, session, turn, side, &actions)) {
                break;
            }
        } else if (!run_other_side_phase(images, session, api, loop, flow->phases.other_side_factory, flow->back,
                                         flow->phases.trait_visuals, token, turn, side, &actions)) {
            break;
        }
        if (!full_stage && side == ENEMY) {
            result_code = actions > 0;
            break;
        }
        void *unused = 0;
        if (!get(api, session->context_repository, "Get", &context) ||
            !call(api->object_class(context), "SaveUsedTime", 0, context, 0, &unused) ||
            !get(api, context, "IsBattleFinished", &finished)) {
            break;
        }
        if (!*(unsigned char *) api->unbox(finished)) {
            if (!change_to_next_phase(api, flow->core, &turn, &side)) {
                break;
            }
            if (action == BATTLE_PHASE) {
                result_code = 1;
                break;
            }
            continue;
        }
settle:
        result_code = full_stage && settle_stage(images, session, exchange, &flow->phases, api, context, flow->core,
                                                 turn, side);
        if (!result_code || !history.keep_open || !history.last) {
            break;
        }
        enum manual_phase_result decision = run_manual_phase(library, images, session, exchange, combat, &flow->phases,
                                                             api, loop, token, &turn, side, &history, 1);
        if (decision != MANUAL_REWOUND) {
            result_code = decision == MANUAL_QUIT;
            break;
        }
        result_code = 0;
        side = OWN;
        resumed = 1;
        phase = 0;
    }
    clear_battle_history(&history);
    return result_code;
}

int execute_exchange(void *library, const struct battle_images *images, const struct battle_session *session,
                     const struct battle_exchange *exchange, const struct combat_services *combat,
                     enum battle_action_mode action) {
    const struct exchange_api api = {
        .object_class = dlsym(library, "il2cpp_object_get_class"),
        .unbox = dlsym(library, "il2cpp_object_unbox"),
        .string_chars = dlsym(library, "il2cpp_string_chars"),
        .string_length = dlsym(library, "il2cpp_string_length"),
        .get_field = dlsym(library, "il2cpp_class_get_field_from_name"),
        .get_field_value = dlsym(library, "il2cpp_field_get_value"),
        .class_name = dlsym(library, "il2cpp_class_get_name"),
    };
    const void *(*get_corlib)(void) = dlsym(library, "il2cpp_get_corlib");
    if (!api.object_class || !api.unbox || !api.string_chars || !api.string_length ||
        !api.get_field || !api.get_field_value || !api.class_name || !get_corlib) {
        return 0;
    }
    const void *corlib = get_corlib();
    struct attack_services attack = {0};
    void *use_case = create_attack_use_case(images, session, exchange, combat, &attack);
    void *token_class = get_class(corlib, "System.Threading", "CancellationToken");
    void *boxed_token = 0;
    struct player_loop loop = {0};
    if (!use_case || !initialize_player_loop(library, images, corlib, &api, &loop) ||
        !call(token_class, "get_None", 0, 0, 0, &boxed_token) || !boxed_token) {
        return 0;
    }
    void *token = api.unbox(boxed_token);
    if (!bind_stage_event_playback(library, images, session, exchange, combat, &attack, &api, &loop)) {
        return 0;
    }
    struct stage_flow flow = {0};
    if (action != BATTLE_EXCHANGE &&
        !initialize_phase_flow(library, images, session, exchange, combat, &attack, &api, action, &flow)) {
        return 0;
    }
    if ((action == BATTLE_STAGE_RUN || action == BATTLE_INTERACTIVE) &&
        !start_player_phase(session, &flow.phases, &api, &loop, token, 1, 0)) {
        return 0;
    }
    if (action == BATTLE_INTERACTIVE) {
        void *unused = 0, *enemy_prediction = 0;
        if (!bind_manual_attack_intercept(library, images) || !bind_enemy_camera_intercept(library, images) ||
            !bind_support_count_intercepts(library, images) ||
            !get(&api, flow.phases.use_cases, "CreateEnemyAttackPredictionUseCase", &enemy_prediction) ||
            !call(api.object_class(exchange->memento), "SaveInitialUnitSetList", 0, exchange->memento, 0, &unused) ||
            !set_manual_defense_context(library, images, session, exchange, &api, enemy_prediction, combat->availability,
                                        flow.phases.use_cases)) {
            return 0;
        }
        int completed = advance_phase(library, images, session, exchange, &flow, combat, &api, &loop, token, action);
        set_manual_defense_context(0, 0, 0, 0, 0, 0, 0, 0);
        return completed;
    }
    struct player_attack input = {0};
    int attacker_before[3], target_before[3], attacker_after[3], target_after[3];
    if (!select_opening_attack(library, images, session, combat, &input)) {
        return 0;
    }
    if (!input.attacker) {
        return action == BATTLE_STAGE_RUN && advance_phase(library, images, session, exchange, &flow,
                                                          combat, &api, &loop, token, action);
    }
    if (!unit_resources(&api, input.attacker, attacker_before) || !unit_resources(&api, input.target, target_before)) {
        return 0;
    }
    void *arguments[] = {input.attacker, input.target, &input.weapon, token};
    void *task = 0;
    printf("REUSE original_exchange_enter\n");
    fflush(0);
    if (!call(api.object_class(use_case), "ExecuteAsync", 4, use_case, arguments, &task) ||
        !finish_task(&api, &loop, "exchange", task, 0, 0) || !unit_resources(&api, input.attacker, attacker_after) ||
        !unit_resources(&api, input.target, target_after)) {
        return 0;
    }
    printf("REUSE original_exchange_return attacker_hp %d->%d en %d->%d mp %d->%d target_hp %d->%d en %d->%d mp %d->%d\n",
           attacker_before[0], attacker_after[0], attacker_before[1], attacker_after[1],
           attacker_before[2], attacker_after[2], target_before[0], target_after[0], target_before[1], target_after[1],
           target_before[2], target_after[2]);
    fflush(0);
    if (attacker_after[1] == attacker_before[1] && target_after[0] == target_before[0]) {
        return 0;
    }
    if (!verify_exchange_settlement(session, exchange, &api, input.target, target_after[0])) {
        return 0;
    }
    return action == BATTLE_EXCHANGE ||
        advance_phase(library, images, session, exchange, &flow, combat, &api, &loop, token, action);
}
