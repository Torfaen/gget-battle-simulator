/* Construct the original battle context and run the opening gameplay services. */

#include "android_runtime.h"

extern void *dlsym(void *library, const char *name);
extern int printf(const char *format, ...);
extern int fflush(void *stream);

struct opening_api {
    void *(*object_class)(void *);
    void *(*unbox)(void *);
    void *(*get_field)(void *, const char *);
    void (*get_field_value)(void *, void *, void *);
    void (*get_static_field)(void *, void *);
    void (*class_init)(void *);
};

static int bind_opening_api(void *library, struct opening_api *api) {
    api->object_class = dlsym(library, "il2cpp_object_get_class");
    api->unbox = dlsym(library, "il2cpp_object_unbox");
    api->get_field = dlsym(library, "il2cpp_class_get_field_from_name");
    api->get_field_value = dlsym(library, "il2cpp_field_get_value");
    api->get_static_field = dlsym(library, "il2cpp_field_static_get_value");
    api->class_init = dlsym(library, "il2cpp_runtime_class_init");
    return api->object_class && api->unbox && api->get_field &&
        api->get_field_value && api->get_static_field && api->class_init;
}

static void *reference_field(const struct opening_api *api, void *object, const char *name) {
    void *field = object ? api->get_field(api->object_class(object), name) : 0;
    void *value = 0;
    if (field) {
        api->get_field_value(object, field, &value);
    }
    return value;
}

static int get(const struct opening_api *api, void *object, const char *name, void **result) {
    return object && call(api->object_class(object), name, 0, object, 0, result) && *result;
}

int initialize_battle_random(void *library, const struct battle_images *images, const struct battle_session *session) {
    struct opening_api api;
    if (!bind_opening_api(library, &api)) {
        return 0;
    }
    void *klass = get_class(images->application, "Eternal.Application", "IngameInitializer");
    void *unused = 0, *random = 0, *seed = 0, *battle_random = 0;
    if (!call(klass, "_CreateStageRandomEntity", 0, session->stage_initializer, 0, &unused) ||
        !get(&api, session->random_repository, "Get", &random) ||
        !get(&api, random, "get_StageSeed", &seed) || !get(&api, random, "get_BattleRandom", &battle_random)) {
        return 0;
    }
    printf("REUSE original_stage_random_initialized seed %llu\n", *(unsigned long long *) api.unbox(seed));
    fflush(0);
    return 1;
}

int initialize_battle_context(void *library, const struct battle_images *images, struct battle_session *session,
                              const struct battle_stage_args *stage) {
    struct opening_api api;
    if (!bind_opening_api(library, &api)) {
        return 0;
    }
    void *resource = 0, *path = 0, *stage_type = 0, *stage_category = 0, *time = 0;
    if (!get(&api, stage->master, "get_StageResourceInfo", &resource) ||
        !get(&api, resource, "get_StageAssetPath", &path) ||
        !get(&api, stage->master, "get_StageType", &stage_type) ||
        !get(&api, stage->master, "get_StageCategoryType", &stage_category) ||
        !get(&api, session->date_time, "GetCurrentDateTime", &time)) {
        return 0;
    }
    void *victory = reference_field(&api, stage->scene, "VictoryConditionPair");
    void *moment = reference_field(&api, stage->scene, "MomentTriggerConditionPair");
    void *secret = reference_field(&api, stage->scene, "SecretConditionPair");
    void *event_areas = reference_field(&api, stage->scene, "EventAreaConditions");
    session->adv_scripts = reference_field(&api, stage->scene, "ADVScriptGroup");
    session->adv_conditions = reference_field(&api, stage->scene, "ADVConditionGroups");
    if (!victory || !moment || !secret || !event_areas) {
        printf("REUSE stage_context_conditions_missing\n");
        return 0;
    }
    void *invasion_class = get_class(images->domain, "Eternal.Domain", "InvasionEventStageExtendId");
    void *no_invasion = invasion_class ? api.get_field(invasion_class, "NONE") : 0;
    if (!no_invasion) {
        return 0;
    }
    api.class_init(invasion_class);
    const struct stage_expectations *expected = session->stage;
    long long invasion_id = expected->invasion_id;
    if (!invasion_id) {
        api.get_static_field(no_invasion, &invasion_id);
    }
    void *tower_stage_id = 0;
    if (expected->tower_stage_id) {
        long long id = expected->tower_stage_id;
        void *id_args[] = {&id};
        void *id_class = get_class(images->domain, "Eternal.Domain", "TowerEventStageId");
        tower_stage_id = construct(id_class, 1, id_args);
        void *tower = 0, *floor = 0, *floor_stage = 0;
        void *floor_stage_key = 0;
        void *floor_args[] = {tower_stage_id};
        if (!tower_stage_id || !get(&api, session->master_repository, "get_TowerEvent", &tower) ||
            !call(api.object_class(tower), "GetMasterTowerEventStage", 1, tower, floor_args, &floor) ||
            !get(&api, floor, "get_StageId", &floor_stage) ||
            !get(&api, floor_stage, "ToLong", &floor_stage_key) ||
            *(long long *) api.unbox(floor_stage_key) != expected->id) {
            return 0;
        }
    }
    long long challenge_id = stage->challenge_id;
    int formation_type = stage->formation_type;
    long long formation_target = stage->formation_target;
    unsigned char is_history = 0, has_strategy_hint = 0, invasion_normal = 0;
    void *arguments[] = {
        stage->id, api.unbox(stage_type), api.unbox(stage_category), path, &formation_type, &formation_target,
        victory, moment, secret, event_areas, tower_stage_id, &invasion_id, &challenge_id, api.unbox(time),
        &is_history, stage->reinforcements, &has_strategy_hint, &invasion_normal, 0, 0,
    };
    void *context_class = get_class(images->domain, "Eternal.Domain", "IngameContextEntity");
    void *repository_class = get_class(images->infrastructure, "Eternal.Infrastructure", "InMemoryIngameContextRepository");
    void *context = construct(context_class, 20, arguments);
    void *repository = construct(repository_class, 0, 0);
    if (!context || !repository) {
        return 0;
    }
    void *text_args[] = {context, stage->master};
    void *save_args[] = {context};
    void *unused = 0, *saved = 0, *saved_id = 0, *finished = 0;
    if (!call(api.object_class(session->stage_initializer), "_InitializeBattleConditionTexts", 2,
              session->stage_initializer, text_args, &unused) ||
        !call(repository_class, "Save", 1, repository, save_args, &unused) ||
        !get(&api, repository, "Get", &saved) || saved != context ||
        !get(&api, context, "get_TargetStageId", &saved_id) || saved_id != stage->id ||
        !get(&api, context, "IsBattleFinished", &finished) || *(unsigned char *) api.unbox(finished)) {
        return 0;
    }
    session->context_repository = repository;
    printf("REUSE original_battle_context_ready tower %lld invasion %lld\n",
           expected->tower_stage_id, expected->invasion_id);
    fflush(0);
    return 1;
}

static int register_invasion_traits(const struct opening_api *api, const struct battle_images *images,
                                    struct battle_session *session) {
    if (!session->stage->invasion_id) {
        return 1;
    }
    void *stage_args[] = {session->master_repository, session->user_data_store, session->user_info_repository,
                         session->user_unit_repository, 0, 0, session->date_time, 0, 0};
    void *stage_class = get_class(images->infrastructure, "Eternal.Infrastructure", "UserStageRepository");
    void *stage = construct(stage_class, 9, stage_args);
    void *service_args[] = {session->unit_repository, session->master_repository, session->trait_service, stage};
    void *klass = get_class(images->application, "Eternal.Application", "IngameInvasionBonusService");
    void *service = stage ? construct(klass, 4, service_args) : 0;
    void *buff = 0, *unused = 0;
    if (!get(api, service, "GetInvasionExtraBuffTraitSet", &buff) ||
        !call(klass, "RegisterTraits", 0, service, 0, &unused)) {
        return 0;
    }
    void *field = api->get_field(api->object_class(buff), "_damageUpRate");
    if (!field) {
        return 0;
    }
    int rate = 0;
    api->get_field_value(buff, field, &rate);
    printf("REUSE original_invasion_traits %lld buff_rate %d\n", session->stage->invasion_id, rate);
    fflush(0);
    return rate == session->stage->invasion_buff_rate;
}

static void *initialize_turn(const struct opening_api *api, const struct battle_images *images,
                             const struct battle_session *session) {
    void *turn_id_class = get_class(images->domain, "Eternal.Domain", "TurnId");
    void *first_field = turn_id_class ? api->get_field(turn_id_class, "FIRST_TURN_CACHE") : 0;
    if (!first_field) {
        return 0;
    }
    api->class_init(turn_id_class);
    void *first = 0;
    api->get_static_field(first_field, &first);
    int side = 0; /* Own, the original initial phase. */
    unsigned short phase_fixed = 0; /* Nullable<bool> with no override. */
    void *turn_args[] = {first, &side, &phase_fixed};
    void *turn_class = get_class(images->domain, "Eternal.Domain", "TurnActivityEntity");
    void *turn = first ? construct(turn_class, 3, turn_args) : 0;
    void *save_args[] = {turn};
    void *unused = 0, *saved = 0, *number = 0, *side_value = 0, *before = 0, *step = 0;
    if (!turn || !call(turn_class, "CreateEmptyStep", 0, turn, 0, &unused) ||
        !call(api->object_class(session->turn_repository), "Save", 1, session->turn_repository, save_args, &unused) ||
        !get(api, session->turn_repository, "FindLatest", &saved) || saved != turn ||
        !get(api, turn, "get_Number", &number) || !get(api, turn, "get_CurrentPhaseBattleSide", &side_value) ||
        !get(api, turn, "IsBeforeBattleStart", &before) || !get(api, turn, "GetCurrentStep", &step)) {
        return 0;
    }
    int actual_turn = *(int *) api->unbox(number);
    int actual_side = *(int *) api->unbox(side_value);
    int before_start = *(unsigned char *) api->unbox(before);
    printf("REUSE original_initial_turn %d side %d before_battle %d\n", actual_turn, actual_side, before_start);
    return actual_turn == 1 && actual_side == side && before_start ? turn : 0;
}

static int check_first_random_key(const struct opening_api *api, const struct battle_images *images, void *turn) {
    void *key_class = get_class(images->domain, "Eternal.Domain", "TurnPhaseStepKey");
    void *first_field = key_class ? api->get_field(key_class, "FIRST_KEY_CACHE") : 0;
    if (!first_field) {
        return 0;
    }
    api->class_init(key_class);
    void *first = 0;
    api->get_static_field(first_field, &first);
    void *key = 0, *same = 0;
    void *args[] = {first};
    if (!first || !get(api, turn, "GetCurrentTurnPhaseStepKey", &key) ||
        !call(key_class, "Equals", 1, key, args, &same) || !same || !*(unsigned char *) api->unbox(same)) {
        return 0;
    }
    printf("REUSE initial_step_matches_random_key\n");
    return 1;
}

static int opening_mp(const struct opening_api *api, const struct battle_images *images,
                      const struct battle_session *session, int *sum) {
    void *id_class = get_class(images->domain, "Eternal.Domain", "IngameUnitId");
    void *repository = session->unit_repository;
    void *repository_class = api->object_class(repository);
    *sum = 0;
    for (int number = 1; number <= session->unit_count; ++number) {
        void *id_args[] = {&number};
        void *id = construct(id_class, 1, id_args);
        void *lookup_args[] = {id};
        void *unit = 0, *mp = 0;
        if (!id || !call(repository_class, "FindById", 1, repository, lookup_args, &unit) ||
            !get(api, unit, "GetCurrentMp", &mp)) {
            return 0;
        }
        *sum += *(int *) api->unbox(mp);
    }
    return 1;
}

static int execute_opening_traits(const struct opening_api *api, const struct battle_images *images,
                                  struct battle_session *session) {
    void *repository = session->unit_repository;
    void *traits_class = api->object_class(session->trait_service);
    void *units = 0, *targets = 0, *unused = 0;
    int mp_before = 0, mp_after_stage = 0, mp_after_first_turn = 0;
    if (!opening_mp(api, images, session, &mp_before) ||
        !get(api, repository, "FindAll", &units) || !get(api, units, "Values", &targets)) {
        return 0;
    }
    void *targets_args[] = {targets};
    if (!call(traits_class, "ExecuteOnStageStart", 1, session->trait_service, targets_args, &unused) ||
        !opening_mp(api, images, session, &mp_after_stage)) {
        return 0;
    }
    printf("REUSE original_stage_start_traits_return\n");
    fflush(0);
    void *view_class = get_class(images->presentation, "Eternal.Presentation", "NoneViewService");
    session->view = construct(view_class, 0, 0);
    if (!session->view) {
        return 0;
    }
    void *aura_class = get_class(images->application, "Eternal.Application", "AuraBuffService");
    void *aura_args[] = {repository, session->trait_service, session->view,
                         session->map_repository, session->turn_repository};
    session->aura_service = construct(aura_class, 5, aura_args);
    if (!session->aura_service ||
        !call(aura_class, "Rebuild", 0, session->aura_service, 0, &unused)) {
        return 0;
    }
    printf("REUSE original_aura_rebuild_return\n");
    void *alive = 0, *alive_targets = 0;
    if (!get(api, repository, "FindAliveOnMap", &alive) || !get(api, alive, "Values", &alive_targets)) {
        return 0;
    }
    void *alive_args[] = {alive_targets};
    if (!call(traits_class, "ExecuteOnFirstTurnStart", 1, session->trait_service, alive_args, &unused) ||
        !opening_mp(api, images, session, &mp_after_first_turn)) {
        return 0;
    }
    void *sets = 0, *count = 0;
    if (!get(api, session->trait_repository, "FindAll", &sets) || !get(api, sets, "get_Count", &count)) {
        return 0;
    }
    int trait_sets = *(int *) api->unbox(count);
    printf("REUSE original_first_turn_traits_return sets %d mp_before %d mp_stage_start %d mp_first_turn %d\n",
           trait_sets, mp_before, mp_after_stage, mp_after_first_turn);
    return trait_sets > 0;
}

int open_battle_turn(void *library, const struct battle_images *images, struct battle_session *session) {
    struct opening_api api;
    if (!bind_opening_api(library, &api)) {
        return 0;
    }
    void *turn = initialize_turn(&api, images, session);
    if (!turn || !check_first_random_key(&api, images, turn)) {
        return 0;
    }
    return register_invasion_traits(&api, images, session) && execute_opening_traits(&api, images, session);
}
