/* Translate manual commands to the original battle use cases. */

#include "android_interactive.h"

extern void *dlsym(void *, const char *);
extern int printf(const char *, ...);
extern int fflush(void *);
extern long read(int, void *, unsigned long);
extern int sscanf(const char *, const char *, ...);
extern int strcmp(const char *, const char *);
extern int memcmp(const void *, const void *, unsigned long);
extern void *malloc(unsigned long);
extern void free(void *);
extern void *fopen(const char *, const char *);
extern int fclose(void *);
extern int fseek(void *, long, int);
extern long ftell(void *);
extern unsigned long fread(void *, unsigned long, unsigned long, void *);
extern unsigned long fwrite(const void *, unsigned long, unsigned long, void *);
extern int ferror(void *);
extern int rename(const char *, const char *);
extern void *memcpy(void *, const void *, unsigned long);

/* The original local save service names IngameAllMemoryPackSaveData by its MD5. */
#define BATTLE_CHECKPOINT "save/GameData/UserData/0B6C1A40DFFE3992946EE8420E602348"
#define BATTLE_TIMELINE "stage/BattleTimeline.bin"
#define MUMU_CHECKPOINT "stage/MuMuCheckpoint.bin"
#define MUMU_RANDOM "stage/MuMuRandom.bin"
#define BATTLE_TIMELINE_MAGIC "GGETSV2"

struct coord { int x, z; };

struct support_choice {
    int number, enabled, preemptive;
    long long weapon;
};

enum { MERSENNE_STATE_WORDS = 624 };

struct battle_random_state {
    struct {
        int cursor;
        unsigned int words[MERSENNE_STATE_WORDS];
    } streams[3];
    unsigned int unity[4];
};

struct mumu_random_state {
    char magic[8];
    unsigned long long stage_seed, undo_count, key[3];
    struct battle_random_state random;
    unsigned int reserved;
};

_Static_assert(sizeof(struct mumu_random_state) == 7568, "MuMu random state layout");

struct forecast_random {
    struct battle_random_state state;
    struct {
        void *stream, *twister, *cursor_field, *array;
    } streams[3];
};

/* Stable on-disk layout for the ARM64 guest and the desktop save reader. */
struct timeline_header {
    char magic[8];
    long long stage_id, invasion_id;
    unsigned int count, reserved;
};

struct checkpoint_header {
    unsigned long long stage_seed, undo_count;
    unsigned int size, moved_unit, is_move, reserved;
    struct battle_random_state random;
    unsigned int unit_count;
};

struct battle_checkpoint {
    struct battle_checkpoint *previous;
    int settled;
    struct checkpoint_header header;
    unsigned char bytes[];
};

enum { CHECKPOINT_MAX_SIZE = 64 * 1024 * 1024 };
_Static_assert(sizeof(struct timeline_header) == 32, "timeline header layout");
_Static_assert(sizeof(struct checkpoint_header) == 7552, "checkpoint header layout");

struct attack_review {
    void *snapshot, *result;
    struct forecast_random random;
    int actor, target, weapon, moved_unit;
    int defender, supporters[3];
    int closed, modified;
    struct save_unit_mp *morale;
};

struct save_unit_mp { int number, mp; };

struct manual_phase {
    const struct battle_session *session;
    const struct battle_exchange *exchange;
    const struct exchange_api *api;
    const struct player_loop *loop;
    void *unit_id_class, *coord_class, *battle_side_extensions;
    void *availability;
    void *caution;
    void *counter_class, *calculator, *participation_class;
    void *move_options;
    void *new_step;
    void *move;
    void *reset_move;
    void *prediction;
    void *attack;
    void *standby;
    void *character_availability, *character_skill, *unit_skill, *skill_service;
    void *unit_skill_targets, *character_skill_targets, *supporter_skill_targets, *supporter_skill;
    void *selected_skill_candidates, *selected_skill_targets;
    int skill_actor, skill_index;
    struct coord supporter_skill_point;
    void *map_creator, *map_select, *map_execute;
    void *support_action_list;
    struct support_choice *support_plan;
    int support_count, support_actor, support_target, support_weapon;
    const void *map_select_open, *map_select_tap;
    void *(*runtime_invoke)(const void *, void *, void **, void **);
    long long map_weapon_id;
    int map_actor, map_direction;
    void *token;
    void *snapshot_name;
    void *random_class;
    struct battle_history *history;
    void *(*new_object)(void *);
    void *(*boxed_field)(void *, void *);
    void (*set_field)(void *, void *, void *);
    unsigned long (*array_length)(void *);
    unsigned int (*array_header)(void);
    void *(*array_new_specific)(void *, unsigned long);
    void *(*element_class)(void *);
    int (*value_size)(void *, unsigned int *);
    void *(*value_box)(void *, void *);
    int moved_unit;
    struct attack_review review;
    int turn, side, width, height;
};

struct unit_info {
    void *unit, *id, *origin;
    struct coord coord;
    int side, ready, post_attack_move, manual;
};

static int tracing_manual_map;

static int list_item(const struct manual_phase *phase, void *list, int index, void **item);
static int list_count(const struct manual_phase *phase, void *list, int *count);

static int get(const struct manual_phase *phase, void *instance, const char *method, void **value) {
    return instance && call(phase->api->object_class(instance), method, 0, instance, 0, value) && *value;
}

static int invoke(const struct manual_phase *phase, void *instance, const char *method, int arity,
                  void **args, void **value) {
    return instance && call(phase->api->object_class(instance), method, arity, instance, args, value);
}

static int field(const struct manual_phase *phase, void *instance, const char *name, void *value) {
    void *member = instance ? phase->api->get_field(phase->api->object_class(instance), name) : 0;
    if (!member) {
        return 0;
    }
    phase->api->get_field_value(instance, member, value);
    return 1;
}

static int get_int(const struct manual_phase *phase, void *instance, const char *method, int *value) {
    void *result = 0;
    if (!get(phase, instance, method, &result)) {
        return 0;
    }
    *value = *(int *) phase->api->unbox(result);
    return 1;
}

static int manual_side(const struct manual_phase *phase, int side, int *manual) {
    if (side == 255) {  /* BattleSideType.None has no operating phase. */
        *manual = 0;
        return 1;
    }
    void *value = 0;
    void *args[] = {&side};
    if (!call(phase->battle_side_extensions, "OperateType", 1, 0, args, &value) || !value) {
        return 0;
    }
    *manual = *(int *) phase->api->unbox(value) == 1;  /* IngameOperateType.Manual */
    return 1;
}

static int battle_sides_match(const struct manual_phase *phase, const char *method, int side, int target) {
    void *value = 0;
    void *args[] = {&side, &target};
    return call(phase->battle_side_extensions, method, 2, 0, args, &value) && value &&
        *(unsigned char *) phase->api->unbox(value);
}

static int get_long(const struct manual_phase *phase, void *instance, const char *method, long long *value) {
    void *result = 0;
    if (!get(phase, instance, method, &result)) {
        return 0;
    }
    *value = *(long long *) phase->api->unbox(result);
    return 1;
}

static int guarded_field(const struct manual_phase *phase, void *instance, const char *name, int *value) {
    void *member = instance ? phase->api->get_field(phase->api->object_class(instance), name) : 0;
    void *boxed = member ? phase->boxed_field(member, instance) : 0;
    void *number = 0;
    if (!boxed || !call(phase->api->object_class(boxed), "ToRaw", 0, phase->api->unbox(boxed), 0, &number) || !number) {
        return 0;
    }
    *value = *(int *) phase->api->unbox(number);
    return 1;
}

static int guarded_bool_field(const struct manual_phase *phase, void *instance, const char *name, int *value) {
    void *member = phase->api->get_field(phase->api->object_class(instance), name);
    void *boxed = member ? phase->boxed_field(member, instance) : 0;
    void *raw = 0;
    if (!boxed || !call(phase->api->object_class(boxed), "ToRaw", 0,
                       phase->api->unbox(boxed), 0, &raw) || !raw) {
        return 0;
    }
    *value = *(unsigned char *) phase->api->unbox(raw) != 0;
    return 1;
}

static int guarded_key(const struct manual_phase *phase, void *id, const char *name, long long *key) {
    void *member = id ? phase->api->get_field(phase->api->object_class(id), name) : 0;
    void *boxed = member ? phase->boxed_field(member, id) : 0;
    void *value = 0;
    if (!boxed || !call(phase->api->object_class(boxed), "ToRaw", 0,
                       phase->api->unbox(boxed), 0, &value) || !value) {
        return 0;
    }
    *key = *(long long *) phase->api->unbox(value);
    return 1;
}

static int skill_key(const struct manual_phase *phase, void *id, long long *key) {
    return guarded_key(phase, id, "_id", key);
}

static void *new_coord(const struct manual_phase *phase, struct coord coord) {
    void *boxed = phase->new_object(phase->coord_class), *unused = 0;
    void *args[] = {&coord.x, &coord.z};
    return boxed && call(phase->coord_class, ".ctor", 2, phase->api->unbox(boxed), args, &unused) ? boxed : 0;
}

static int get_bool(const struct manual_phase *phase, void *instance, const char *method, int *value) {
    void *result = 0;
    if (!get(phase, instance, method, &result)) {
        return 0;
    }
    *value = *(unsigned char *) phase->api->unbox(result) != 0;
    return 1;
}

static int task(const struct manual_phase *phase, const char *label, void *instance, const char *method,
                int arity, void **args, void **result) {
    void *awaitable = 0;
    return invoke(phase, instance, method, arity, args, &awaitable) &&
        finish_task(phase->api, phase->loop, label, awaitable, result, 0);
}

static int read_coord(const struct manual_phase *phase, void *boxed, struct coord *coord) {
    void *text = 0;
    if (!call(phase->coord_class, "ToString", 0, phase->api->unbox(boxed), 0, &text) || !text) {
        return 0;
    }
    char label[48];
    format_managed_text(text, label, sizeof(label));
    return sscanf(label, "(%d,%d)", &coord->x, &coord->z) == 2;
}

static int find_battle_unit(const struct manual_phase *phase, int number, void **id, void **unit) {
    void *ctor_args[] = {&number};
    *id = construct(phase->unit_id_class, 1, ctor_args);
    void *args[] = {*id};
    return *id && invoke(phase, phase->session->unit_repository, "FindById", 1, args, unit);
}

static int lookup(const struct manual_phase *phase, void *origins, int number, struct unit_info *info) {
    *info = (struct unit_info) {0};
    if (number <= 0 || number > phase->session->unit_count) {
        return 1;
    }
    if (!find_battle_unit(phase, number, &info->id, &info->unit)) {
        return 0;
    }
    if (!info->unit) {
        return 1;
    }
    void *args[] = {info->id};
    if (!invoke(phase, origins, "FindByUnitId", 1, args, &info->origin) ||
        !get_int(phase, info->unit, "get_BattleSideType", &info->side) ||
        !manual_side(phase, info->side, &info->manual) ||
        !get_bool(phase, info->unit, "IsReadyStep", &info->ready) ||
        !get_bool(phase, info->unit, "get_CanMoveAfterAnyAttack", &info->post_attack_move)) {
        return 0;
    }
    if (info->origin && number == phase->moved_unit) {
        void *turn = 0, *step = 0, *id = 0;
        int operating = 0;
        /* MoveUseCase stages a free move. StandbyUseCase or AttackUseCase commits it to the map. */
        if (!get(phase, phase->session->turn_repository, "FindLatest", &turn) ||
            !get(phase, turn, "GetCurrentStep", &step) || !get(phase, step, "get_OperatingIngameUnitId", &id) ||
            !get_int(phase, id, "ToInt", &operating) || operating != number ||
            !get(phase, step, "get_FreeMoveUnitCoord", &info->origin)) {
            return 0;
        }
    }
    if (info->origin && !read_coord(phase, info->origin, &info->coord)) {
        printf("REUSE manual_invalid_coord %d\n", number);
        return 0;
    }
    return 1;
}

static int map_origins(const struct manual_phase *phase, void **map, void **origins) {
    return get(phase, phase->session->map_repository, "FindLatest", map) &&
        get(phase, *map, "get_UnitMapOriginPlaceDict", origins);
}

static int emit_footprint(const struct manual_phase *phase, const char *event, int number, const struct unit_info *info) {
    int direction, count;
    void *coords = 0, *iterator = 0;
    void *args[] = {phase->api->unbox(info->origin)};
    if (!get_int(phase, info->unit, "get_Direction", &direction) ||
        !invoke(phase, info->unit, "OccupiedCoords", 1, args, &coords) ||
        !get_int(phase, coords, "get_Count", &count) || count <= 0 ||
        !get(phase, coords, "GetEnumerator", &iterator)) {
        return 0;
    }
    void *iterator_class = phase->api->object_class(iterator);
    void *value = phase->api->unbox(iterator);
    printf("UI\t%s\t%d\t%d", event, number, direction);
    for (int i = 0; i < count; ++i) {
        void *more = 0, *current = 0;
        struct coord cell;
        if (!call(iterator_class, "MoveNext", 0, value, 0, &more) || !more ||
            !*(unsigned char *) phase->api->unbox(more) ||
            !call(iterator_class, "get_Current", 0, value, 0, &current) || !current ||
            !read_coord(phase, current, &cell)) {
            printf("\n");
            return 0;
        }
        printf("\t%d\t%d", cell.x, cell.z);
    }
    printf("\n");
    return 1;
}

static void emit_map_objectives(const struct manual_phase *phase);
static void emit_unit_aura(const struct manual_phase *phase, const struct unit_info *unit, int number);
static void emit_npc_aura_effects(const struct manual_phase *phase, void *npc_buff, int number);
static void emit_move_markers(const struct manual_phase *phase, const struct unit_info *unit, void *possible);

static int support_remaining(const struct manual_phase *phase, void *unit, int *attack, int *defense) {
    int type, used, maximum;
    if (!get_int(phase, unit, "GetSupportableType", &type) ||
        !get_int(phase, unit, "GetCurrentSupportCount", &used) ||
        !get_int(phase, unit, "GetMaxSupportCount", &maximum)) {
        return 0;
    }
    int remaining = maximum > used ? maximum - used : 0;
    *attack = type == 1 ? remaining : 0;
    *defense = type == 2 ? remaining : 0;
    return 1;
}

static int scripted_board_dirty;

void stage_event_board_changed(void) {
    scripted_board_dirty = 1;
}

static int read_board_size(struct manual_phase *phase, void *root) {
    void *map = 0, *chips = 0;
    return get(phase, root, "get_CurrentMap", &map) && field(phase, map, "MapChipList", &chips) &&
        guarded_field(phase, chips, "Width", &phase->width) && guarded_field(phase, chips, "Height", &phase->height) &&
        phase->width > 0 && phase->height > 0;
}

static void emit_unit_effects(const struct manual_phase *phase, const struct unit_info *unit, int number) {
    void *sets = 0;
    int count;
    unsigned char owner_only = 0;
    void *args[] = {unit->id, &owner_only};
    if (!invoke(phase, phase->session->trait_service, "FindBuffDebuffTraits", 2, args, &sets) ||
        !list_count(phase, sets, &count)) {
        return;
    }
    for (int i = 0; i < count; ++i) {
        void *set = 0, *effects = 0, *resource = 0, *set_name = 0;
        int kind, effect_count;
        long long source = 0;
        if (!list_item(phase, sets, i, &set) || !guarded_field(phase, set, "BuffDebuffType", &kind) ||
            (kind != 1 && kind != 2) || !get(phase, set, "get_Values", &effects) ||
            !list_count(phase, effects, &effect_count)) {
            continue;
        }
        field(phase, set, "ResourceId", &resource);
        field(phase, set, "Name", &set_name);
        guarded_key(phase, set, "SourceId", &source);
        char icon[80];
        format_managed_text(resource, icon, sizeof(icon));
        for (int j = 0; j < effect_count; ++j) {
            void *effect = 0, *name = 0, *description = 0;
            int active, duration, remaining, value, aura, type;
            long long id = 0;
            if (!list_item(phase, effects, j, &effect) || !get_bool(phase, effect, "IsActive", &active) || !active ||
                !get_int(phase, effect, "get_TraitType", &type) || !get_int(phase, effect, "GetDurationType", &duration) ||
                !get_int(phase, effect, "GetRemainingValue", &remaining) || !get_int(phase, effect, "GetTraitValue", &value) ||
                !get_bool(phase, effect, "IsAuraBuff", &aura)) {
                continue;
            }
            guarded_key(phase, effect, "MasterTraitId", &id);
            field(phase, effect, "Name", &name);
            field(phase, effect, "Description", &description);
            char label[256], text[1024];
            format_managed_text(name ? name : set_name, label, sizeof(label));
            format_managed_text(description, text, sizeof(text));
            printf("UI\tUNIT_EFFECT\t%d\t%d\t%lld\t%lld\t%d\t%d\t%d\t%d\t%d\t%s\t%s\t%s\n",
                   number, kind, id, source, type, duration, remaining, value, aura, icon, label, text);
        }
    }
}

static int emit_state(const struct manual_phase *phase, void *origins, int during_other_phase, int focus) {
    void *root = 0, *map = 0, *area = 0, *asset = 0;
    int x, z, width, height;
    if (!get(phase, phase->session->map_repository, "FindLatest", &root) ||
        !get(phase, root, "get_CurrentMap", &map) ||
        !get(phase, map, "get_CurrentArea", &area) ||
        !guarded_field(phase, area, "X", &x) || !guarded_field(phase, area, "Z", &z) ||
        !guarded_field(phase, area, "Width", &width) || !guarded_field(phase, area, "Height", &height)) {
        x = z = 0;
        width = phase->width;
        height = phase->height;
        printf("REUSE manual_map_area_failed\n");
    }
    field(phase, map, "_assetBundleName", &asset);
    char asset_name[128];
    format_managed_text(asset, asset_name, sizeof(asset_name));
    printf("UI\t%s\t%lld\t%d\t%d\t%d\t%d\t%d\n",
           during_other_phase ? "BOARD" : "STATE", phase->session->stage->id, phase->turn,
           phase->width, phase->height, during_other_phase ? 0 : (int) phase->history->count, phase->moved_unit);
    if (!during_other_phase) {
        printf("UI\tREDO\t%u\n", phase->history->redo_count);
    }
    printf("UI\tMAP_AREA\t%d\t%d\t%d\t%d\t%s\n", x, z, width, height, asset_name);
    emit_map_objectives(phase);
    for (int number = 1; number <= phase->session->unit_count; ++number) {
        struct unit_info info;
        if (!lookup(phase, origins, number, &info)) {
            printf("REUSE manual_unit_lookup_failed %d\n", number);
            return 0;
        }
        if (!info.origin) {
            continue;
        }
        int hp, maximum, en, max_en, mp;
        void *user_unit = 0, *master = 0, *master_id = 0, *name = 0;
        if (!get_int(phase, info.unit, "get_CurrentHp", &hp) ||
            !get_int(phase, info.unit, "GetBuffedMaxHp", &maximum) ||
            !get_int(phase, info.unit, "get_CurrentEn", &en) ||
            !get_int(phase, info.unit, "GetBuffedMaxEn", &max_en) ||
            !get_int(phase, info.unit, "GetCurrentMp", &mp) ||
            !get(phase, info.unit, "get_UserUnit", &user_unit) || !get(phase, user_unit, "get_MasterUnit", &master) ||
            !get(phase, master, "Eternal.Domain.ISortableId.get_SortableId", &master_id) ||
            !invoke(phase, user_unit, "GetName", 0, 0, &name)) {
            printf("REUSE manual_unit_state_failed %d\n", number);
            return 0;
        }
        char label[256];
        format_managed_text(name, label, sizeof(label));
        printf("UI\tUNIT\t%d\t%d\t%d\t%d\t%d\t%d\t%d\t%d\t%d\t%lld\t%s\n", number, info.side, info.coord.x,
               info.coord.z, info.ready, hp, maximum, en, mp, *(long long *) phase->api->unbox(master_id), label);
        printf("UI\tUNIT_MAX_EN\t%d\t%d\n", number, max_en);
        printf("UI\tUNIT_MANUAL\t%d\t%d\n", number, info.manual);
        void *sfs = 0;
        if (get(phase, user_unit, "get_SfsResourceId", &sfs)) {
            char sfs_resource[64];
            format_managed_text(sfs, sfs_resource, sizeof(sfs_resource));
            if (sfs_resource[0]) {
                int standby = 0;
                get_int(phase, info.unit, "get_UnitStandbyType", &standby);
                printf("UI\tUNIT_SFS\t%d\t%s\t%d\t%d\n", number, sfs_resource, standby,
                       phase->session->stage_terrain);
            }
        }
        if (!emit_footprint(phase, "FOOTPRINT", number, &info)) {
            printf("REUSE manual_footprint_failed %d\n", number);
            return 0;
        }
        long long character;
        int sp, role, support_attack, support_defense;
        if (!get_long(phase, info.unit, "Eternal.Domain.IIngameUnitKey.get_CharacterId", &character) ||
            !get_int(phase, info.unit, "get_CurrentSp", &sp) ||
            !get_int(phase, master, "get_RoleType", &role) ||
            !support_remaining(phase, info.unit, &support_attack, &support_defense)) {
            printf("REUSE manual_unit_details_failed %d\n", number);
            return 0;
        }
        printf("UI\tUNIT_DETAIL\t%d\t%lld\t%d\t%d\t%d\t%d\n", number, character, role, sp,
               support_attack, support_defense);
        int barrier, attack;
        if (!get_int(phase, info.unit, "get_CurrentHpBarrier", &barrier) ||
            !get_int(phase, info.unit, "GetBuffedAttack", &attack)) {
            return 0;
        }
        printf("UI\tUNIT_TACTICAL\t%d\t%d\t%d\t%d\n", number, barrier, info.post_attack_move, attack);
        emit_unit_effects(phase, &info, number);
        if (!during_other_phase) {
            emit_unit_aura(phase, &info, number);
        }
    }
    scripted_board_dirty = 0;
    if (during_other_phase) {
        if (focus) {
            printf("UI\tBOARD_READY\t%d\n", focus);
        } else {
            printf("UI\tBOARD_READY\n");
        }
    }
    return 1;
}

static int is_player_unit(const struct manual_phase *phase, const struct unit_info *unit) {
    return unit->origin && unit->manual && battle_sides_match(phase, "IsSamePhase", phase->side, unit->side);
}

static int can_operate(const struct manual_phase *phase, const struct unit_info *unit) {
    return is_player_unit(phase, unit) && unit->ready && !unit->post_attack_move;
}

static int can_finish_move(const struct manual_phase *phase, const struct unit_info *unit) {
    return is_player_unit(phase, unit) && (unit->ready || unit->post_attack_move);
}

static int hostile(const struct manual_phase *phase, const struct unit_info *actor, const struct unit_info *target) {
    return target->origin && battle_sides_match(phase, "IsOpposition", actor->side, target->side);
}

static int move_places(const struct manual_phase *phase, int number, const struct unit_info *unit,
                       void **places, void **possible) {
    int immobile = 0;
    if (!get_bool(phase, unit->unit, "IsMovementInability", &immobile)) {
        return 0;
    }
    *places = 0;
    *possible = 0;
    if (number == phase->moved_unit || (immobile && !phase->caution)) {
        return 1;
    }
    void *args[] = {unit->id};
    return invoke(phase, phase->move_options, "Create", 1, args, possible) && *possible &&
        (immobile || (field(phase, *possible, "MovableOriginPlaces", places) && *places));
}

static int can_move(const struct manual_phase *phase, void *places, void *coord, int *allowed) {
    *allowed = 0;
    if (!places) {
        return 1;
    }
    void *args[] = {phase->api->unbox(coord)};
    void *result = 0;
    if (!invoke(phase, places, "Contains", 1, args, &result) || !result) {
        return 0;
    }
    *allowed = *(unsigned char *) phase->api->unbox(result) != 0;
    return 1;
}

static int weapon_list(const struct manual_phase *phase, const struct unit_info *from,
                       const struct unit_info *to, void **weapons, int *count) {
    void *terrain = 0;
    if (!get(phase, from->unit, "get_StandbyTerrainType", &terrain)) {
        return 0;
    }
    void *args[] = {from->unit, from->origin, terrain, to->unit, to->origin};
    return call_boxed(phase->api->object_class(phase->availability), "FindAvailableWeapons", 5,
                      phase->availability, args, weapons) && get_int(phase, *weapons, "Count", count);
}

static int weapon_at(const struct manual_phase *phase, void *weapons, int index, void **weapon, long long *id) {
    void *args[] = {&index};
    void *key = 0;
    if (!invoke(phase, weapons, "FindByIndex", 1, args, weapon) || !get(phase, *weapon, "get_WeaponId", &key)) {
        return 0;
    }
    *id = *(long long *) phase->api->unbox(key);
    return 1;
}

static void emit_weapon_info(const struct manual_phase *phase, int number, long long key,
                             void *weapon, int is_map);
static void emit_weapon_catalog(const struct manual_phase *phase, const struct unit_info *unit, int number,
                                int catalog, void *map_entity);
static int emit_coords(const struct manual_phase *phase, void *coords, const char *event, int slot,
                       const struct coord *skip);
static int emit_map_coords(const struct manual_phase *phase, void *coords, const char *event, int slot);

static int emit_options(const struct manual_phase *phase, void *map, void *origins, int number) {
    struct unit_info actor;
    printf("UI\tOPTIONS\t%d\n", number);
    if (!lookup(phase, origins, number, &actor)) {
        return 0;
    }
    if (!can_finish_move(phase, &actor)) {
        return 1;
    }
    void *places = 0, *possible = 0;
    if (!move_places(phase, number, &actor, &places, &possible)) {
        return 0;
    }
    if (places) {
        void *coords = 0;
        if (!get(phase, places, "AsCoordSet", &coords) ||
            !emit_coords(phase, coords, "MOVE", -1, &actor.coord)) {
            return 0;
        }
    }
    printf("UI\tMOVE_READY\n");
    fflush(0);
    emit_move_markers(phase, &actor, possible);
    if (!can_operate(phase, &actor)) {
        return 1;
    }
    emit_weapon_catalog(phase, &actor, number, 0, map);
    for (int number = 1; number <= phase->session->unit_count; ++number) {
        struct unit_info target;
        if (!lookup(phase, origins, number, &target)) {
            return 0;
        }
        if (!hostile(phase, &actor, &target)) {
            continue;
        }
        void *weapons = 0;
        int count;
        if (!weapon_list(phase, &actor, &target, &weapons, &count)) {
            return 0;
        }
        for (int i = 0; i < count; ++i) {
            void *weapon = 0, *name = 0;
            long long id;
            if (!weapon_at(phase, weapons, i, &weapon, &id) || !invoke(phase, weapon, "GetName", 0, 0, &name)) {
                printf("REUSE manual_weapon_lookup_failed %d %d\n", number, i);
                return 0;
            }
            char label[256];
            format_managed_text(name, label, sizeof(label));
            printf("UI\tWEAPON\t%d\t%d\t%lld\t%s\n", number, i, id, label);
        }
    }
    return 1;
}

static int write_snapshot(const struct manual_phase *phase) {
    void *unused = 0;
    void *args[] = {phase->snapshot_name};
    return invoke(phase, phase->exchange->memento, "SaveLatestStepStartSnapShot", 1, args, &unused);
}

static int forecast_random_state(const struct manual_phase *phase, struct forecast_random *state, int restore);

/* The original loader can reapply NPC morale traits while rebuilding the map. */
static int checkpoint_morale(const struct manual_phase *phase, struct save_unit_mp *units, int restore) {
    for (int i = 0; i < phase->session->unit_count; ++i) {
        int number = restore ? units[i].number : i + 1;
        if (restore && !number) {
            continue;
        }
        void *id = 0, *unit = 0;
        if (number != i + 1 || !find_battle_unit(phase, number, &id, &unit)) {
            return 0;
        }
        if (restore) {
            int mp = 0;
            void *args[] = {&units[i].mp}, *unused = 0;
            if (!unit || !get_int(phase, unit, "GetCurrentMp", &mp) ||
                (mp != units[i].mp && !invoke(phase, unit, "SetMp", 1, args, &unused))) {
                return 0;
            }
        } else {
            units[i] = (struct save_unit_mp) {.number = unit ? number : 0};
            if (unit && !get_int(phase, unit, "GetCurrentMp", &units[i].mp)) {
                return 0;
            }
        }
    }
    return 1;
}

static unsigned long checkpoint_size(const struct checkpoint_header *header) {
    return header->size + sizeof(struct save_unit_mp) * header->unit_count;
}

static struct battle_checkpoint *capture_checkpoint(const struct manual_phase *phase, int moved_unit,
                                                    const struct attack_review *review) {
    void *entity = 0;
    long long seed, undo_count;
    struct forecast_random random;
    if (!get(phase, phase->session->random_repository, "Get", &entity) ||
        !get_long(phase, entity, "get_StageSeed", &seed) || !get_long(phase, entity, "get_UndoCount", &undo_count) ||
        (!review && !forecast_random_state(phase, &random, 0))) {
        return 0;
    }
    void *file = fopen(battle_path(BATTLE_CHECKPOINT), "rb");
    if (!file) {
        return 0;
    }
    long size = fseek(file, 0, 2) ? -1 : ftell(file);
    unsigned int units = phase->session->unit_count;
    unsigned long prefix = units * sizeof(struct save_unit_mp);
    struct battle_checkpoint *entry = size > 0 && size <= CHECKPOINT_MAX_SIZE && !fseek(file, 0, 0)
        ? malloc(sizeof(*entry) + prefix + (unsigned long) size) : 0;
    int loaded = entry && fread(entry->bytes + prefix, 1, (unsigned long) size, file) == (unsigned long) size;
    fclose(file);
    if (!loaded) {
        free(entry);
        return 0;
    }
    entry->previous = 0;
    entry->settled = 0;
    entry->header = (struct checkpoint_header) {
        .stage_seed = (unsigned long long) seed, .undo_count = (unsigned long long) undo_count,
        .size = (unsigned int) size, .moved_unit = (unsigned int) moved_unit,
        .random = review ? review->random.state : random.state, .unit_count = units,
    };
    if (review) {
        memcpy(entry->bytes, review->morale, prefix);
    } else if (!checkpoint_morale(phase, (struct save_unit_mp *) entry->bytes, 0)) {
        free(entry);
        return 0;
    }
    return entry;
}

static int push_checkpoint(struct manual_phase *phase, struct battle_checkpoint *entry) {
    if (!entry) {
        return 0;
    }
    entry->previous = phase->history->last;
    phase->history->last = entry;
    ++phase->history->count;
    printf("REUSE manual_checkpoint_saved %u depth %u\n", entry->header.size, phase->history->count);
    return 1;
}

static struct battle_checkpoint *pop_checkpoint(struct battle_checkpoint **last, unsigned int *count) {
    struct battle_checkpoint *entry = *last;
    *last = entry->previous;
    --*count;
    return entry;
}

static void discard_checkpoint(struct battle_history *history) {
    free(pop_checkpoint(&history->last, &history->count));
}

static void clear_redo(struct battle_history *history) {
    while (history->redo) {
        free(pop_checkpoint(&history->redo, &history->redo_count));
    }
}

void clear_battle_history(struct battle_history *history) {
    while (history->last) {
        discard_checkpoint(history);
    }
    clear_redo(history);
}

static int save_action(struct manual_phase *phase) {
    return write_snapshot(phase) && push_checkpoint(phase, capture_checkpoint(phase, phase->moved_unit, 0));
}

static int execute_standby(struct manual_phase *phase, void *unit) {
    unsigned char ignore_chance = 0;
    void *args[] = {unit, phase->token, &ignore_chance};
    if (!task(phase, "manual_standby", phase->standby, "ExecuteAsync", 3, args, 0)) {
        return 0;
    }
    phase->moved_unit = 0;
    return 1;
}

static int commit_movement(struct manual_phase *phase, void *origins) {
    struct unit_info moved;
    return !phase->moved_unit || (lookup(phase, origins, phase->moved_unit, &moved) && moved.unit &&
        execute_standby(phase, moved.unit));
}

static int prepare_action(struct manual_phase *phase, void *origins, int number, void *unit) {
    if (phase->moved_unit == number) {
        return 1;
    }
    void *args[] = {unit}, *unused = 0;
    return commit_movement(phase, origins) && invoke(phase, phase->new_step, "Execute", 1, args, &unused);
}

static int start_action(struct manual_phase *phase, void *origins, int number, void *unit) {
    return save_action(phase) && prepare_action(phase, origins, number, unit);
}

enum command_result { COMMAND_FAILED, COMMAND_OK, COMMAND_ILLEGAL };

static enum command_result standby_unit(struct manual_phase *phase, void *origins, int number) {
    struct unit_info actor;
    if (!lookup(phase, origins, number, &actor)) {
        return COMMAND_FAILED;
    }
    if (!can_finish_move(phase, &actor)) {
        return COMMAND_ILLEGAL;
    }
    if (!start_action(phase, origins, number, actor.unit) || !execute_standby(phase, actor.unit)) {
        return COMMAND_FAILED;
    }
    printf("UI\tSTANDBY\t%d\n", number);
    return COMMAND_OK;
}

static enum command_result move_unit(struct manual_phase *phase, void *origins, int number, struct coord destination) {
    struct unit_info actor;
    if (!lookup(phase, origins, number, &actor)) {
        return COMMAND_FAILED;
    }
    if (!can_finish_move(phase, &actor) || (destination.x == actor.coord.x && destination.z == actor.coord.z)) {
        return COMMAND_ILLEGAL;
    }
    void *places = 0, *possible = 0;
    int allowed;
    void *coord = new_coord(phase, destination);
    if (!coord || !move_places(phase, number, &actor, &places, &possible) ||
        !can_move(phase, places, coord, &allowed)) {
        return COMMAND_FAILED;
    }
    if (!allowed) {
        return COMMAND_ILLEGAL;
    }
    void *args[] = {actor.unit, phase->api->unbox(actor.origin), phase->api->unbox(coord), phase->token};
    if (!start_action(phase, origins, number, actor.unit) ||
        !task(phase, "manual_move", phase->move, "ExecuteAsync", 4, args, 0)) {
        return COMMAND_FAILED;
    }
    phase->history->last->header.is_move = 1;
    phase->moved_unit = number;
    struct unit_info moved;
    if (!lookup(phase, origins, number, &moved) || !moved.origin ||
        moved.coord.x != destination.x || moved.coord.z != destination.z) {
        printf("REUSE manual_move_destination_mismatch unit %d\n", number);
        return COMMAND_FAILED;
    }
    printf("REUSE manual_move turn %d unit %d from (%d,%d) to (%d,%d)\n", phase->turn, number,
           actor.coord.x, actor.coord.z, destination.x, destination.z);
    return COMMAND_OK;
}

static int current_step(const struct manual_phase *phase, void **step) {
    void *turn = 0;
    return get(phase, phase->session->turn_repository, "FindLatest", &turn) &&
        get(phase, turn, "GetCurrentStep", step);
}

static const struct manual_phase *marker_phase;
static int aura_owner;
struct map_gauge { int valid, current, predicted; };
static struct {
    const struct manual_phase *phase;
    struct map_gauge *values;
    int count;
} map_gauges;

void report_map_gauge(void *view, int number, void *hp, void *en, const void *method) {
    (void) view;
    (void) en;
    (void) method;
    int current, predicted;
    if (map_gauges.phase && number > 0 && number <= map_gauges.count && hp &&
        guarded_field(map_gauges.phase, hp, "Current", &current) &&
        guarded_field(map_gauges.phase, hp, "Prediction", &predicted)) {
        map_gauges.values[number - 1] = (struct map_gauge) {1, current, predicted};
    }
}

void reset_map_gauges(void *view, const void *method) {
    (void) view;
    (void) method;
    if (map_gauges.phase) {
        for (int i = 0; i < map_gauges.count; ++i) {
            map_gauges.values[i].valid = 0;
        }
    }
}

void report_board_marker(void *view, int marker, void *coords, int side, int animate, const void *method) {
    (void) view;
    (void) side;
    (void) animate;
    (void) method;
    if (!marker_phase || aura_owner) {
        return;
    }
    /* DisplaySupportAndCautionUseCase emits MoveAssert, AssistDefence and MapWeaponWarningRange. */
    if (marker == 24) {
        if (!emit_map_coords(marker_phase, coords, "SUPPORT_CELL", -1)) {
            printf("REUSE manual_support_cells_failed\n");
        }
    } else if ((marker == 23 || marker == 57) && !emit_map_coords(marker_phase, coords, "DANGER", marker)) {
        printf("REUSE manual_danger_coords_failed %d\n", marker);
    }
}

void report_board_aura(void *view, void *dto_list, const void *method) {
    (void) view;
    (void) method;
    int count;
    if (!marker_phase || !aura_owner || !list_count(marker_phase, dto_list, &count)) {
        return;
    }
    for (int i = 0; i < count; ++i) {
        void *dto = 0, *coords = 0;
        int type;
        if (list_item(marker_phase, dto_list, i, &dto) && get_int(marker_phase, dto, "get_BuffType", &type) &&
            get(marker_phase, dto, "get_AreaCoords", &coords)) {
            printf("UI\tAURA_TYPE\t%d\t%d\n", aura_owner, type);
            emit_map_coords(marker_phase, coords, "AURA_CELL", aura_owner);
        }
    }
}

static void emit_unit_aura(const struct manual_phase *phase, const struct unit_info *unit, int number) {
    void *aura = 0, *npc_buff = 0, *unused = 0;
    int valid = 0;
    if (!get(phase, unit->unit, "get_AuraBuff", &aura)) {
        return;
    }
    if (get(phase, aura, "get_NpcBuff", &npc_buff)) {
        emit_npc_aura_effects(phase, npc_buff, number);
    }
    if (!phase->caution || !get_bool(phase, aura, "IsValid", &valid) || !valid) {
        return;
    }
    void *args[] = {unit->id};
    marker_phase = phase;
    aura_owner = number;
    invoke(phase, phase->session->aura_service, "ShowOnly", 1, args, &unused);
    aura_owner = 0;
    marker_phase = 0;
}

static void emit_npc_aura_effects(const struct manual_phase *phase, void *npc_buff, int number) {
    void *range = 0, *set = 0, *traits = 0, *data = 0, *targets = 0;
    int kind, aura_type, minimum, maximum, count;
    if (!get_int(phase, npc_buff, "get_BuffType", &kind) ||
        !get_int(phase, npc_buff, "get_UnitBuffType", &aura_type) ||
        !get(phase, npc_buff, "get_AroundRange", &range) ||
        !guarded_field(phase, range, "MinRange", &minimum) ||
        !guarded_field(phase, range, "MaxRange", &maximum) ||
        !get(phase, npc_buff, "get_TraitSet", &set) ||
        !get(phase, set, "get_Values", &traits) || !list_count(phase, traits, &count)) {
        return;
    }
    if (field(phase, npc_buff, "_mMapNpcBuff", &data) && data) {
        get(phase, data, "get_TargetTypes", &targets);
    }
    char target_types[100];
    format_managed_text(targets, target_types, sizeof(target_types));
    for (int i = 0; i < count; ++i) {
        void *trait = 0, *name = 0, *description = 0;
        long long id;
        int type, value, timing;
        if (!list_item(phase, traits, i, &trait) ||
            !get_long(phase, trait, "get_MasterTraitId", &id) ||
            !get_int(phase, trait, "get_TraitType", &type) ||
            !get_int(phase, trait, "get_TraitValue", &value) ||
            !get_int(phase, trait, "get_ActionTimingType", &timing)) {
            continue;
        }
        get(phase, trait, "get_Name", &name);
        get(phase, trait, "get_Description", &description);
        char label[256], detail[1024];
        format_managed_text(name, label, sizeof(label));
        format_managed_text(description, detail, sizeof(detail));
        printf("UI\tAURA_EFFECT\t%d\t%d\t%d\t%lld\t%d\t%d\t%d\t%d\t%d\t%s\t%s\t%s\n",
               number, kind, aura_type, id, type, value, timing, minimum, maximum, target_types, label, detail);
    }
}

static void emit_move_markers(const struct manual_phase *phase, const struct unit_info *unit, void *possible) {
    void *step = 0, *unused = 0;
    if (!phase->caution || !current_step(phase, &step)) {
        return;
    }
    void *old_id = 0, *old_possible = 0, *id_field = 0, *possible_field = 0;
    if (possible) {
        void *klass = phase->api->object_class(step);
        id_field = phase->api->get_field(klass, "<OperatingIngameUnitId>k__BackingField");
        possible_field = phase->api->get_field(klass, "_stepUnitPossibleData");
        if (!id_field || !possible_field) {
            return;
        }
        phase->api->get_field_value(step, id_field, &old_id);
        phase->api->get_field_value(step, possible_field, &old_possible);
        /* The original display use case reads these fields. Restore them after the read. */
        phase->set_field(step, id_field, unit->id);
        phase->set_field(step, possible_field, possible);
    }
    marker_phase = phase;
    invoke(phase, phase->caution, "_ShowDefenseSupportableRange", 0, 0, &unused);
    invoke(phase, phase->caution, "_ShowEnemyAttackRange", 0, 0, &unused);
    invoke(phase, phase->caution, "_ShowMapWeaponRange", 0, 0, &unused);
    marker_phase = 0;
    if (id_field) {
        phase->set_field(step, id_field, old_id);
        phase->set_field(step, possible_field, old_possible);
    }
}

static enum command_result cancel_move(struct manual_phase *phase, void *origins, int number) {
    if (number != phase->moved_unit || !number) {
        return COMMAND_ILLEGAL;
    }
    struct unit_info actor;
    void *step = 0, *initial = 0, *unused = 0;
    struct coord start;
    if (!lookup(phase, origins, number, &actor) || !actor.origin ||
        !current_step(phase, &step) || !get(phase, step, "get_InitUnitCoord", &initial) ||
        !read_coord(phase, initial, &start)) {
        return COMMAND_FAILED;
    }
    const struct battle_checkpoint *last = phase->history->last;
    int discard_move = last && last->header.is_move && !last->header.moved_unit;
    if (!discard_move && !save_action(phase)) {
        return COMMAND_FAILED;
    }
    void *args[] = {actor.unit};
    if (!invoke(phase, phase->reset_move, "Execute", 1, args, &unused) ||
        !invoke(phase, step, "CancelBattlePrediction", 0, 0, &unused)) {
        return COMMAND_FAILED;
    }
    phase->moved_unit = 0;
    if (!lookup(phase, origins, number, &actor) || !actor.origin ||
        actor.coord.x != start.x || actor.coord.z != start.z) {
        return COMMAND_FAILED;
    }
    if (discard_move) {
        discard_checkpoint(phase->history);
    }
    printf("UI\tMOVE_CANCELLED\t%d\t%d\t%d\n", number, start.x, start.z);
    return COMMAND_OK;
}

static int list_item(const struct manual_phase *phase, void *list, int index, void **item) {
    void *args[] = {&index};
    return invoke(phase, list, "get_Item", 1, args, item) && *item;
}

static int list_count(const struct manual_phase *phase, void *list, int *count) {
    if (!list) {
        *count = 0;
        return 1;
    }
    return get_int(phase, list, "get_Count", count);
}

static unsigned int weapon_attributes(const struct manual_phase *phase, void *weapon, const char *getter) {
    void *values = 0;
    unsigned int mask = 0;
    if (get(phase, weapon, getter, &values)) {
        for (int i = 1; i <= 4; ++i) {
            void *args[] = {&i}, *result = 0;
            if (invoke(phase, values, "Contains", 1, args, &result) &&
                result && *(unsigned char *) phase->api->unbox(result)) {
                mask |= 1u << i;
            }
        }
    }
    return mask;
}

static void emit_weapon_text(int number, long long key, const char *event, void *message) {
    char text[1024];
    format_managed_text(message, text, sizeof(text));
    if (text[0]) {
        printf("UI\t%s\t%d\t%lld\t%s\n", event, number, key, text);
    }
}

static void emit_weapon_info(const struct manual_phase *phase, int number, long long key,
                             void *weapon, int is_map) {
    int power, en, level, min = 0, max = 0;
    if (!get_int(phase, weapon, "GetPowerWithLevelOnly", &power) ||
        !get_int(phase, weapon, "GetEnConsumption", &en) ||
        !get_int(phase, weapon, "get_WeaponLevel", &level)) {
        return;
    }
    void *range = 0, *traits = 0, *values = 0, *text = 0, *addition = 0;
    if (!is_map && get(phase, weapon, "GetRangeWithCustomCore", &range)) {
        guarded_field(phase, range, "MinRange", &min);
        guarded_field(phase, range, "MaxRange", &max);
    }
    unsigned int attributes = weapon_attributes(phase, weapon, "GetWeaponAttributeTypes");
    unsigned int attack_types = weapon_attributes(phase, weapon, "GetAttackAttributeTypes");
    printf("UI\tWEAPON_INFO\t%d\t%lld\t%d\t%d\t%d\t%d\t%d\t%u\t%u\n",
           number, key, power, en, level, min, max, attributes, attack_types);
    if (get(phase, weapon, "GetWeaponSpecDescription", &text)) {
        emit_weapon_text(number, key, "WEAPON_TEXT", text);
    }
    int count;
    if (get(phase, weapon, "GetTraitList", &traits) &&
        field(phase, traits, "Values", &values) && list_count(phase, values, &count)) {
        for (int i = 0; i < count; ++i) {
            long long trait;
            void *entry = 0, *description = 0;
            if (list_item(phase, values, i, &entry) && guarded_key(phase, entry, "Id", &trait) &&
                field(phase, entry, "Description", &description)) {
                char line[1024];
                format_managed_text(description, line, sizeof(line));
                printf("UI\tWEAPON_EFFECT\t%d\t%lld\t%lld\t%s\n", number, key, trait, line);
            }
        }
    }
    if (get(phase, weapon, "GetWeaponTraitAddition", &addition) &&
        get(phase, addition, "GetDescription", &text)) {
        emit_weapon_text(number, key, "WEAPON_TEXT", text);
    }
}

static void emit_weapon_catalog(const struct manual_phase *phase, const struct unit_info *unit, int number,
                                int catalog, void *map_entity) {
    for (int map = 0; map <= catalog; ++map) {
        void *weapons = 0;
        int count;
        if (!get(phase, unit->unit, map ? "GetUnitMapWeaponList" : "GetAllWeapons", &weapons) ||
            !get_int(phase, weapons, "Count", &count)) {
            continue;
        }
        for (int i = 0; i < count; ++i) {
            void *weapon = 0;
            long long key;
            if (map) {
                void *args[] = {&i};
                if (!invoke(phase, weapons, "GetByIndex", 1, args, &weapon) ||
                    !get_long(phase, weapon, "get_WeaponId", &key)) {
                    continue;
                }
            } else if (!weapon_at(phase, weapons, i, &weapon, &key)) {
                continue;
            }
            emit_weapon_info(phase, number, key, weapon, map);
            void *name = 0;
            char label[256];
            get(phase, weapon, "GetName", &name);
            format_managed_text(name, label, sizeof(label));
            if (catalog) {
                printf("UI\tUNIT_WEAPON\t%d\t%lld\t%d\t%s\n", number, key, map, label);
            } else {
                printf("UI\tSELECT_WEAPON\t%d\t%lld\t%s\n", number, key, label);
                void *range = 0, *places = 0, *coords = 0;
                if (get(phase, weapon, "GetRangeWithCustomCore", &range)) {
                    void *args[] = {unit->unit, range, phase->api->unbox(unit->origin)};
                    if (invoke(phase, map_entity, "CalculateEffectivePlaces", 3, args, &places) &&
                        get(phase, places, "AsCoordSet", &coords)) {
                        emit_map_coords(phase, coords, "SELECT_WEAPON_RANGE", -1);
                    }
                }
            }
        }
    }
}

static int map_weapon_service(const struct manual_phase *phase, void *weapon, int *type, void **service) {
    if (!get_int(phase, weapon, "get_MapWeaponRangeType", type)) {
        return 0;
    }
    void *args[] = {type};
    return invoke(phase, phase->map_creator, "Create", 1, args, service) && *service;
}

static int map_weapon_allowed(const struct manual_phase *phase, const struct unit_info *unit,
                              int number, void *weapon, void *service, int *allowed) {
    unsigned char moved = phase->moved_unit == number;
    void *args[] = {unit->unit, weapon, &moved}, *value = 0;
    if (!invoke(phase, service, "IsMapWeaponAvailable", 3, args, &value) || !value) {
        return 0;
    }
    *allowed = *(unsigned char *) phase->api->unbox(value) != 0;
    return 1;
}

static int emit_map_weapons(const struct manual_phase *phase, void *origins, int number) {
    struct unit_info unit;
    if (!lookup(phase, origins, number, &unit)) {
        return 0;
    }
    if (!can_operate(phase, &unit)) {
        return 1;
    }
    void *weapons = 0;
    int count;
    if (!invoke(phase, unit.unit, "GetUnitMapWeaponList", 0, 0, &weapons)) {
        return 0;
    }
    if (!weapons) {
        return 1;
    }
    if (!get_int(phase, weapons, "Count", &count)) {
        printf("REUSE manual_map_weapon_count_failed %d %s\n", number,
               phase->api->class_name(phase->api->object_class(weapons)));
        return 0;
    }
    for (int i = 0; i < count; ++i) {
        void *args[] = {&i}, *weapon = 0, *service = 0, *name = 0;
        int type, allowed, slots, ammo;
        long long key;
        if (!invoke(phase, weapons, "GetByIndex", 1, args, &weapon) || !weapon ||
            !map_weapon_service(phase, weapon, &type, &service) ||
            !map_weapon_allowed(phase, &unit, number, weapon, service, &allowed) ||
            !get_long(phase, weapon, "get_WeaponId", &key) ||
            !get_int(phase, weapon, "get_MapWeaponImpactPointMaxSelectCount", &slots)) {
            printf("REUSE manual_map_weapon_list_failed %d %d\n", number, i);
            return 0;
        }
        void *ammo_args[] = {weapon}, *value = 0;
        if (!invoke(phase, unit.unit, "GetMapWeaponRemainingAmmo", 1, ammo_args, &value) || !value) {
            printf("REUSE manual_map_weapon_ammo_failed %d %d\n", number, i);
            return 0;
        }
        ammo = *(int *) phase->api->unbox(value);
        if (!invoke(phase, weapon, "GetName", 0, 0, &name)) {
            return 0;
        }
        char label[256];
        format_managed_text(name, label, sizeof(label));
        printf("UI\tMAP_WEAPON\t%lld\t%d\t%d\t%d\t%d\t%s\n", key, type, allowed, slots, ammo, label);
        emit_weapon_info(phase, number, key, weapon, 1);
    }
    return 1;
}

static int emit_coords(const struct manual_phase *phase, void *coords, const char *event, int slot,
                       const struct coord *skip) {
    if (!coords) {
        return 1;
    }
    int count;
    void *iterator = 0;
    if (!get_int(phase, coords, "get_Count", &count) || !get(phase, coords, "GetEnumerator", &iterator)) {
        return 0;
    }
    void *klass = phase->api->object_class(iterator);
    void *value = phase->api->unbox(iterator);
    for (int i = 0; i < count; ++i) {
        void *more = 0, *item = 0;
        struct coord cell;
        if (!call(klass, "MoveNext", 0, value, 0, &more) || !more || !*(unsigned char *) phase->api->unbox(more) ||
            !call(klass, "get_Current", 0, value, 0, &item) || !item || !read_coord(phase, item, &cell)) {
            return 0;
        }
        if (skip && cell.x == skip->x && cell.z == skip->z) {
            continue;
        }
        if (slot < 0) {
            printf("UI\t%s\t%d\t%d\n", event, cell.x, cell.z);
        } else {
            printf("UI\t%s\t%d\t%d\t%d\n", event, slot, cell.x, cell.z);
        }
    }
    return 1;
}

static int emit_map_coords(const struct manual_phase *phase, void *coords, const char *event, int slot) {
    return emit_coords(phase, coords, event, slot, 0);
}

static void emit_map_objectives(const struct manual_phase *phase) {
    void *objectives = 0;
    int count;
    if (!get(phase, phase->exchange->conditions, "GetActiveTargetArea", &objectives) ||
        !list_count(phase, objectives, &count)) {
        printf("REUSE manual_map_objectives_unavailable\n");
        return;
    }
    for (int i = 0; i < count; ++i) {
        void *dto = 0, *cells = 0, *pin = 0, *center = 0;
        int type, show_area, show_pin;
        if (!list_item(phase, objectives, i, &dto) || !dto || !guarded_field(phase, dto, "TargetAreaType", &type) ||
            !guarded_bool_field(phase, dto, "IsAreaVisible", &show_area) ||
            !guarded_bool_field(phase, dto, "IsPinVisible", &show_pin)) {
            continue;
        }
        if (show_area && (!field(phase, dto, "AreaCoords", &cells) ||
                         !emit_map_coords(phase, cells, "MAP_OBJECTIVE", type))) {
            printf("REUSE manual_map_objective_cells_unavailable %d\n", type);
        }
        if (show_pin && field(phase, dto, "PinArea", &pin) && get(phase, pin, "GetCenterPosition", &center)) {
            const float *position = phase->api->unbox(center);  /* Unity Vector3. */
            printf("UI\tMAP_PIN\t%d\t%.3f\t%.3f\n", type, position[0], position[2]);
        }
    }
}

static int emit_selected_map_coords(const struct manual_phase *phase, void *coords) {
    unsigned int alignment = 0;
    if (!coords) {
        return 0;
    }
    int width = phase->value_size(phase->coord_class, &alignment);
    if (width <= 0) {
        return 0;
    }
    char *first = (char *) coords + phase->array_header();
    unsigned long count = phase->array_length(coords);
    for (unsigned long i = 0; i < count; ++i) {
        struct coord coord;
        void *boxed = phase->value_box(phase->coord_class, first + i * (unsigned long) width);
        if (!boxed || !read_coord(phase, boxed, &coord)) {
            return 0;
        }
        printf("UI\tMAP_POINT\t%d\t%d\n", coord.x, coord.z);
    }
    return 1;
}

static int emit_map_slots(const struct manual_phase *phase, const struct unit_info *unit,
                          void *weapon, void *service, void *slots, void *coords) {
    void *slot_class = phase->api->object_class(slots);
    unsigned int alignment;
    int slot_size = phase->value_size(phase->element_class(slot_class), &alignment);
    int coord_size = phase->value_size(phase->coord_class, &alignment);
    int editing;
    if (slot_size <= coord_size || coord_size <= 0 ||
        !field(phase, phase->map_select, "_editingSlotIndex", &editing)) {
        return 0;
    }
    void *single_slot = phase->array_new_specific(slot_class, 1);
    if (!single_slot) {
        return 0;
    }
    unsigned long header = phase->array_header();
    unsigned long selected = 0, count = phase->array_length(coords);
    unsigned long capacity = phase->array_length(slots);
    printf("UI\tMAP_EDIT\t%d\n", editing);
    for (unsigned long i = 0; i < capacity; ++i) {
        unsigned char *slot = (unsigned char *) slots + header + i * (unsigned long) slot_size;
        /* The original Nullable<Coord> stores HasValue first. SelectedCoords preserves slot order. */
        if (!*slot) {
            continue;
        }
        if (selected >= count) {
            return 0;
        }
        struct coord point;
        void *value = (char *) coords + header + selected * (unsigned long) coord_size;
        void *boxed = phase->value_box(phase->coord_class, value);
        if (!boxed || !read_coord(phase, boxed, &point)) {
            return 0;
        }
        ++selected;
        printf("UI\tMAP_POINT\t%d\t%d\n", point.x, point.z);
        printf("UI\tMAP_SLOT\t%d\t%d\t%d\n", (int) i, point.x, point.z);
        memcpy((char *) single_slot + header, slot, (unsigned long) slot_size);
        unsigned char first = 0;
        void *args[] = {unit->unit, weapon, single_slot, &first, 0}, *dto = 0, *range = 0;
        if (!invoke(phase, service, "CreateMapWeaponEffectRangeDto", 5, args, &dto) || !dto ||
            !invoke(phase, dto, "get_ImpactRangeCoords", 0, 0, &range) ||
            !emit_map_coords(phase, range, "MAP_SLOT_EFFECT", (int) i)) {
            return 0;
        }
    }
    return selected == count;
}

static void emit_map_target_ids(const struct manual_phase *phase, void *dto) {
    void *ids = 0;
    if (!invoke(phase, dto, "get_TargetUnitIds", 0, 0, &ids) || !ids) {
        return;
    }
    int count = 0;
    if (!list_count(phase, ids, &count)) {
        return;
    }
    for (int i = 0; i < count; ++i) {
        void *item = 0;
        if (list_item(phase, ids, i, &item)) {
            printf("UI\tMAP_TARGET\t%d\n", *(int *) phase->api->unbox(item));
        }
    }
}

static int emit_map_selectable(const struct manual_phase *phase, const struct unit_info *unit,
                               void *weapon, void *service) {
    for (int z = 0; z < phase->height; ++z) {
        for (int x = 0; x < phase->width; ++x) {
            void *coord = new_coord(phase, (struct coord) {x, z}), *valid = 0;
            void *args[] = {unit->unit, weapon, coord ? phase->api->unbox(coord) : 0};
            if (!coord || !invoke(phase, service, "CanTapCell", 3, args, &valid) || !valid) {
                return 0;
            }
            if (*(unsigned char *) phase->api->unbox(valid)) {
                printf("UI\tMAP_TAP\t%d\t%d\n", x, z);
            }
        }
    }
    return 1;
}

static int skill_options(const struct manual_phase *phase, const struct unit_info *unit, void **pilot_skills) {
    void *pilot = 0;
    return get(phase, unit->unit, "get_UserCharacter", &pilot) &&
        invoke(phase, pilot, "GetAcquiredCharacterSkillList", 0, 0, pilot_skills);
}

static int character_skill_option(const struct manual_phase *phase, const struct unit_info *unit,
                                  void *skills, int index, void **id, int *available, void **name) {
    void *option = 0;
    if (!list_item(phase, skills, index, &option) || !field(phase, option, "Id", id) ||
        !field(phase, option, "Name", name)) {
        return 0;
    }
    void *reasons = 0, *enabled = 0;
    void *args[] = {option, unit->unit, &reasons};
    if (!invoke(phase, phase->character_availability, "TryCheckAvailability", 3, args, &enabled) || !enabled) {
        return 0;
    }
    *available = *(unsigned char *) phase->api->unbox(enabled) != 0;
    return 1;
}

static int unit_skill_option(const struct manual_phase *phase, const struct unit_info *unit, void **skill,
                             int *available, int *remaining) {
    int empty, limit, used;
    void *id = 0, *can_use = 0;
    *available = *remaining = 0;
    if (!invoke(phase, unit->unit, "get_UnitSkill", 0, 0, skill)) {
        return 0;
    }
    if (!*skill) {
        return 1;
    }
    if (!get_bool(phase, *skill, "get_IsEmpty", &empty)) {
        return 0;
    }
    if (empty) {
        *skill = 0;
        return 1;
    }
    void *availability[] = {unit->unit, *skill};
    if (!invoke(phase, phase->skill_service, "IsUnitSkillAvailable", 2, availability, &can_use) ||
        !can_use || !get(phase, *skill, "get_Id", &id) ||
        !get_int(phase, *skill, "get_UsageLimit", &limit)) {
        return 0;
    }
    void *usage_args[] = {unit->id, id}, *usage = 0;
    if (!invoke(phase, phase->exchange->memento, "GetUnitSkillUsageCount", 2, usage_args, &usage) || !usage) {
        return 0;
    }
    used = *(int *) phase->api->unbox(usage);
    *remaining = limit > used ? limit - used : 0;
    *available = *(unsigned char *) phase->api->unbox(can_use) != 0;
    return 1;
}

enum { SUPPORTER_SKILL = -2 };
enum supporter_skill_range { SUPPORTER_WHOLE_MAP = 1, SUPPORTER_IMPACT_RANGE = 2 };

struct supporter_skill_option {
    void *supporter, *skill, *group;
    int range, used;
};

static int find_supporter_skill(const struct manual_phase *phase, const struct unit_info *unit,
                                struct supporter_skill_option *option) {
    *option = (struct supporter_skill_option) {0};
    if (!invoke(phase, unit->unit, "get_IngameUnitGroup", 0, 0, &option->group)) {
        return 0;
    }
    if (!option->group) {
        return 1;
    }
    void *args[] = {option->group};
    if (!invoke(phase, phase->session->supporter_repository, "FindByIngameUnitGroup", 1, args,
                &option->supporter)) {
        return 0;
    }
    if (!option->supporter) {
        return 1;
    }
    return invoke(phase, option->supporter, "get_ActiveSkill", 0, 0, &option->skill) &&
        (!option->skill ||
         (guarded_field(phase, option->skill, "RangeType", &option->range) &&
          get_bool(phase, option->supporter, "get_IsUsed", &option->used)));
}

static int supporter_skill_available(const struct supporter_skill_option *option) {
    return !option->used && (option->range == SUPPORTER_WHOLE_MAP || option->range == SUPPORTER_IMPACT_RANGE);
}

static int emit_supporter_skill(const struct manual_phase *phase, const struct unit_info *unit) {
    struct supporter_skill_option option;
    if (!find_supporter_skill(phase, unit, &option)) {
        return 0;
    }
    if (!option.range) {
        return 1;
    }
    void *master = 0, *master_id = 0, *id = 0, *name = 0, *label = 0, *description = 0;
    long long supporter_key, skill_key;
    if (!get(phase, option.supporter, "get_MasterSupporter", &master) ||
        !get(phase, master, "get_Id", &master_id) || !get_long(phase, master_id, "ToLong", &supporter_key) ||
        !invoke(phase, option.supporter, "get_Name", 0, 0, &name) ||
        !field(phase, option.skill, "Id", &id) || !get_long(phase, id, "ToLong", &skill_key) ||
        !field(phase, option.skill, "Name", &label) || !field(phase, option.skill, "Description", &description)) {
        return 0;
    }
    char supporter_name[256], skill_name[256], detail[1024];
    format_managed_text(name, supporter_name, sizeof(supporter_name));
    format_managed_text(label, skill_name, sizeof(skill_name));
    format_managed_text(description, detail, sizeof(detail));
    printf("UI\tSUPPORTER_SKILL\t%d\t%d\t%lld\t%lld\t%s\t%s\t%s\n",
           supporter_skill_available(&option), !option.used, supporter_key, skill_key,
           supporter_name, skill_name, detail);
    return 1;
}

static int emit_skills(const struct manual_phase *phase, void *origins, int number) {
    struct unit_info unit;
    if (!lookup(phase, origins, number, &unit)) {
        return 0;
    }
    if (!is_player_unit(phase, &unit) || unit.post_attack_move) {
        return 1;
    }
    void *skills = 0, *skill = 0;
    int count, enabled, remaining;
    if (!skill_options(phase, &unit, &skills)) {
        printf("REUSE manual_character_skill_list_failed %d\n", number);
        return 0;
    }
    if (!list_count(phase, skills, &count)) {
        printf("REUSE manual_character_skill_count_failed %d\n", number);
        return 0;
    }
    if (!unit_skill_option(phase, &unit, &skill, &enabled, &remaining)) {
        printf("REUSE manual_unit_skill_option_failed %d\n", number);
        return 0;
    }
    for (int i = 0; i < count; ++i) {
        void *id = 0, *name = 0;
        int available;
        if (!character_skill_option(phase, &unit, skills, i, &id, &available, &name)) {
            return 0;
        }
        if (id) {
            long long key;
            if (!skill_key(phase, id, &key)) {
                return 0;
            }
            char label[256];
            format_managed_text(name, label, sizeof(label));
            printf("UI\tCHAR_SKILL\t%d\t%d\t%lld\t%s\n", i, available, key, label);
        }
    }
    if (skill) {
        void *name = 0, *id = 0;
        long long key;
        if (!invoke(phase, skill, "get_Name", 0, 0, &name) ||
            !get(phase, skill, "get_Id", &id) || !skill_key(phase, id, &key)) {
            return 0;
        }
        char label[256];
        format_managed_text(name, label, sizeof(label));
        printf("UI\tUNIT_SKILL\t%d\t%d\t%lld\t%s\n", enabled, remaining, key, label);
    }
    return emit_supporter_skill(phase, &unit);
}

static int emit_skill_unit_set(const struct manual_phase *phase, void *origins, void *targets) {
    void *ids = 0;
    if (!get(phase, targets, "AsIngameUnitIdSet", &ids)) {
        return 0;
    }
    for (int number = 1; number <= phase->session->unit_count; ++number) {
        struct unit_info unit;
        if (!lookup(phase, origins, number, &unit)) {
            return 0;
        }
        if (!unit.origin) {
            continue;
        }
        void *args[] = {unit.id}, *contains = 0;
        if (!invoke(phase, ids, "Contains", 1, args, &contains) || !contains) {
            return 0;
        }
        if (*(unsigned char *) phase->api->unbox(contains)) {
            printf("UI\tSKILL_TARGET\t%d\n", number);
        }
    }
    return 1;
}

static int supporter_skill_targets(const struct manual_phase *phase, const struct supporter_skill_option *option,
                                   void *coord, void **targets) {
    void *args[] = {option->supporter, phase->api->unbox(coord)}, *result = 0;
    int ranged = option->range == SUPPORTER_IMPACT_RANGE;
    return invoke(phase, phase->supporter_skill_targets,
                  ranged ? "FindTargetsByImpactCoord" : "FindTargetsByWholeMap", ranged ? 2 : 1, args, &result) &&
        field(phase, result, "Targets", targets) && *targets;
}

static int place_supporter_skill_actor(const struct manual_phase *phase, void *map, const struct unit_info *unit,
                                      void *from, void *to) {
    void *args[] = {unit->unit, phase->api->unbox(from), phase->api->unbox(to)}, *unused = 0;
    return invoke(phase, map, "MoveUnit", 3, args, &unused);
}

static int supporter_skill_committed_origin(const struct manual_phase *phase, void *origins,
                                            const struct unit_info *unit, int actor, void **origin) {
    *origin = 0;
    if (phase->moved_unit != actor) {
        return 1;
    }
    void *args[] = {unit->id};
    return invoke(phase, origins, "FindByUnitId", 1, args, origin) && *origin;
}

static int supporter_skill_center(const struct manual_phase *phase, const struct supporter_skill_option *option,
                                  const struct unit_info *unit, int *allowed) {
    *allowed = 0;
    if (!unit->origin) {
        return 1;
    }
    void *group = 0, *same = 0;
    void *args[] = {option->group};
    if (!invoke(phase, unit->unit, "get_IngameUnitGroup", 0, 0, &group)) {
        return 0;
    }
    if (!group) {
        return 1;
    }
    if (!invoke(phase, group, "IsSameGroup", 1, args, &same) || !same) {
        return 0;
    }
    *allowed = *(unsigned char *) phase->api->unbox(same) != 0;
    return 1;
}

static enum command_result select_supporter_skill(struct manual_phase *phase, void *map, void *origins,
                                                   int actor, int center, struct coord point) {
    struct unit_info unit;
    struct supporter_skill_option option;
    if (!lookup(phase, origins, actor, &unit)) {
        return COMMAND_FAILED;
    }
    if (!is_player_unit(phase, &unit) || unit.post_attack_move ||
        (phase->moved_unit && phase->moved_unit != actor)) {
        return COMMAND_ILLEGAL;
    }
    if (!find_supporter_skill(phase, &unit, &option)) {
        return COMMAND_FAILED;
    }
    if (!supporter_skill_available(&option)) {
        return COMMAND_ILLEGAL;
    }
    int ranged = option.range == SUPPORTER_IMPACT_RANGE;
    if (!center) {
        point = unit.coord;
    } else {
        if (!ranged || phase->skill_actor != actor || phase->skill_index != SUPPORTER_SKILL) {
            return COMMAND_ILLEGAL;
        }
        struct unit_info candidate;
        int allowed;
        if (!lookup(phase, origins, center, &candidate) ||
            !supporter_skill_center(phase, &option, &candidate, &allowed)) {
            return COMMAND_FAILED;
        }
        if (!allowed) {
            return COMMAND_ILLEGAL;
        }
        void *position = new_coord(phase, point), *cells = 0, *contains = 0;
        void *at[] = {phase->api->unbox(candidate.origin)};
        if (!position) {
            return COMMAND_FAILED;
        }
        void *args[] = {phase->api->unbox(position)};
        if (!invoke(phase, candidate.unit, "OccupiedCoords", 1, at, &cells) ||
            !invoke(phase, cells, "Contains", 1, args, &contains) || !contains) {
            return COMMAND_FAILED;
        }
        if (!*(unsigned char *) phase->api->unbox(contains)) {
            return COMMAND_ILLEGAL;
        }
    }
    void *coord = new_coord(phase, point), *targets = 0, *range = 0, *committed = 0;
    if (!coord || !supporter_skill_committed_origin(phase, origins, &unit, actor, &committed)) {
        return COMMAND_FAILED;
    }
    /* The original selector reads map occupancy. Show it the same staged position as the board. */
    if (committed && !place_supporter_skill_actor(phase, map, &unit, committed, unit.origin)) {
        return COMMAND_FAILED;
    }
    int found = supporter_skill_targets(phase, &option, coord, &targets);
    if ((committed && !place_supporter_skill_actor(phase, map, &unit, unit.origin, committed)) || !found) {
        return COMMAND_FAILED;
    }
    if (ranged) {
        void *effect_range = 0, *cells = 0;
        void *args[] = {phase->api->unbox(coord)};
        if (!field(phase, option.skill, "EffectRange", &effect_range) ||
            !invoke(phase, effect_range, "CalculateRangeCoords", 1, args, &cells)) {
            return COMMAND_FAILED;
        }
        void *existing[] = {cells};
        if (!invoke(phase, map, "GetExistingCoords", 1, existing, &range)) {
            return COMMAND_FAILED;
        }
    }
    printf("UI\tSKILL_PREVIEW\t%d\n", ranged);
    if (ranged) {
        printf("UI\tSKILL_POINT\t%d\t%d\n", point.x, point.z);
        for (int number = 1; number <= phase->session->unit_count; ++number) {
            struct unit_info candidate;
            int allowed;
            if (!lookup(phase, origins, number, &candidate) ||
                !supporter_skill_center(phase, &option, &candidate, &allowed)) {
                return COMMAND_FAILED;
            }
            if (allowed) {
                printf("UI\tSKILL_CANDIDATE\t%d\n", number);
            }
        }
    }
    phase->skill_actor = actor;
    phase->skill_index = SUPPORTER_SKILL;
    phase->supporter_skill_point = point;
    return emit_skill_unit_set(phase, origins, targets) && emit_map_coords(phase, range, "SKILL_RANGE", -1)
        ? COMMAND_OK : COMMAND_FAILED;
}

static enum command_result use_supporter_skill(struct manual_phase *phase, void *map, void *origins, int actor) {
    if (phase->skill_actor != actor || phase->skill_index != SUPPORTER_SKILL ||
        (phase->moved_unit && phase->moved_unit != actor)) {
        return COMMAND_ILLEGAL;
    }
    struct unit_info unit;
    struct supporter_skill_option option;
    if (!lookup(phase, origins, actor, &unit)) {
        return COMMAND_FAILED;
    }
    if (!is_player_unit(phase, &unit) || unit.post_attack_move) {
        return COMMAND_ILLEGAL;
    }
    if (!find_supporter_skill(phase, &unit, &option)) {
        return COMMAND_FAILED;
    }
    if (!supporter_skill_available(&option)) {
        return COMMAND_ILLEGAL;
    }
    void *coord = new_coord(phase, phase->supporter_skill_point), *targets = 0, *ids = 0, *id = 0, *committed = 0;
    if (!coord || !get(phase, option.supporter, "get_Id", &id) ||
        !supporter_skill_committed_origin(phase, origins, &unit, actor, &committed) || !save_action(phase) ||
        (committed && !place_supporter_skill_actor(phase, map, &unit, committed, unit.origin))) {
        return COMMAND_FAILED;
    }
    int count;
    void *args[] = {id, phase->api->unbox(coord), phase->token};
    int ok = supporter_skill_targets(phase, &option, coord, &targets) &&
        get(phase, targets, "AsIngameUnitIdSet", &ids) && list_count(phase, ids, &count) &&
        (!count || task(phase, "manual_supporter_skill", phase->supporter_skill, "ExecuteAsync", 3, args, 0));
    /* Restore occupancy so the player can still confirm or cancel the staged move. */
    if ((committed && !place_supporter_skill_actor(phase, map, &unit, unit.origin, committed)) || !ok) {
        return COMMAND_FAILED;
    }
    if (!count) {
        discard_checkpoint(phase->history);
        return COMMAND_ILLEGAL;
    }
    printf("UI\tSKILL_USED\t%d\t%d\n", actor, SUPPORTER_SKILL);
    return COMMAND_OK;
}

static int emit_unit_skill_ids(const struct manual_phase *phase, void *ids, const char *event) {
    int count;
    if (!list_count(phase, ids, &count)) {
        return 0;
    }
    for (int i = 0; i < count; ++i) {
        void *id = 0;
        int number;
        if (!list_item(phase, ids, i, &id) || !get_int(phase, id, "ToInt", &number)) {
            return 0;
        }
        printf("UI\t%s\t%d\n", event, number);
    }
    return 1;
}

static enum command_result select_skill(struct manual_phase *phase, void *map, void *origins,
                                        int number, int index, int target) {
    struct unit_info unit;
    if (!lookup(phase, origins, number, &unit)) {
        return COMMAND_FAILED;
    }
    if (!is_player_unit(phase, &unit) || unit.post_attack_move) {
        return COMMAND_ILLEGAL;
    }
    void *skill = 0, *range = 0;
    int enabled;
    if (index >= 0) {
        void *skills = 0, *id = 0, *name = 0;
        int count;
        if (!skill_options(phase, &unit, &skills) || !list_count(phase, skills, &count)) {
            return COMMAND_FAILED;
        }
        if (index >= count || target) {
            return COMMAND_ILLEGAL;
        }
        if (!character_skill_option(phase, &unit, skills, index, &id, &enabled, &name) ||
            !list_item(phase, skills, index, &skill)) {
            return COMMAND_FAILED;
        }
        if (!enabled || !id) {
            return COMMAND_ILLEGAL;
        }
        void *find[] = {skill, unit.unit}, *targets = 0;
        if (!invoke(phase, phase->character_skill_targets, "FindTargetList", 2, find, &targets)) {
            return COMMAND_FAILED;
        }
        printf("UI\tSKILL_PREVIEW\t0\n");
        if (!emit_skill_unit_set(phase, origins, targets)) {
            return COMMAND_FAILED;
        }
        void *effect_range = 0;
        if (field(phase, skill, "EffectRange", &effect_range) && effect_range) {
            void *args[] = {unit.unit, effect_range, phase->api->unbox(unit.origin)}, *places = 0;
            if (!invoke(phase, map, "CalculateEffectivePlaces", 3, args, &places) ||
                !invoke(phase, places, "AsCoordSet", 0, 0, &range)) {
                return COMMAND_FAILED;
            }
        }
    } else {
        int remaining, type;
        if (!unit_skill_option(phase, &unit, &skill, &enabled, &remaining)) {
            return COMMAND_FAILED;
        }
        if (!enabled) {
            return COMMAND_ILLEGAL;
        }
        if (!get_int(phase, skill, "get_MultiTargetType", &type)) {
            return COMMAND_FAILED;
        }
        if (target) {
            if (!type || phase->skill_actor != number || phase->skill_index != index ||
                !phase->selected_skill_candidates || !phase->selected_skill_targets) {
                return COMMAND_ILLEGAL;
            }
            void *id_args[] = {&target};
            void *id = construct(phase->unit_id_class, 1, id_args), *chosen = 0;
            void *args[] = {skill, phase->selected_skill_candidates, phase->selected_skill_targets, id};
            if (!id || !invoke(phase, phase->unit_skill_targets, "Select", 4, args, &chosen) || !chosen) {
                return COMMAND_FAILED;
            }
            phase->selected_skill_targets = chosen;
        } else {
            void *find[] = {unit.unit, skill};
            if (!invoke(phase, phase->unit_skill_targets, "FindTargetCandidates", 2, find,
                        &phase->selected_skill_candidates) || !phase->selected_skill_candidates) {
                return COMMAND_FAILED;
            }
            void *args[] = {skill, phase->selected_skill_candidates};
            if (!invoke(phase, phase->unit_skill_targets, "AutoSelect", 2, args, &phase->selected_skill_targets) ||
                !phase->selected_skill_targets) {
                return COMMAND_FAILED;
            }
        }
        printf("UI\tSKILL_PREVIEW\t%d\n", type != 0);
        if (!emit_unit_skill_ids(phase, phase->selected_skill_candidates, "SKILL_CANDIDATE") ||
            !emit_unit_skill_ids(phase, phase->selected_skill_targets, "SKILL_TARGET")) {
            return COMMAND_FAILED;
        }
        int ranged;
        if (!get_bool(phase, skill, "get_IsRangeSkill", &ranged)) {
            return COMMAND_FAILED;
        }
        if (ranged) {
            void *at[] = {phase->api->unbox(unit.origin)}, *area = 0;
            if (!invoke(phase, unit.unit, "OccupiedArea", 1, at, &area) || !area) {
                return COMMAND_FAILED;
            }
            void *args[] = {area};
            if (!invoke(phase, skill, "CalculateRange", 1, args, &range)) {
                return COMMAND_FAILED;
            }
        }
    }
    phase->skill_actor = number;
    phase->skill_index = index;
    return emit_map_coords(phase, range, "SKILL_RANGE", -1) ? COMMAND_OK : COMMAND_FAILED;
}

static enum command_result use_skill(struct manual_phase *phase, void *origins, int number, int index) {
    struct unit_info unit;
    if (!lookup(phase, origins, number, &unit)) {
        return COMMAND_FAILED;
    }
    if (!is_player_unit(phase, &unit) || unit.post_attack_move) {
        return COMMAND_ILLEGAL;
    }
    unsigned char camera = 0;
    if (index >= 0) {
        int count, enabled;
        void *skills = 0, *id = 0, *name = 0;
        if (!skill_options(phase, &unit, &skills) || !list_count(phase, skills, &count)) {
            return COMMAND_FAILED;
        }
        if (index >= count) {
            return COMMAND_ILLEGAL;
        }
        if (!character_skill_option(phase, &unit, skills, index, &id, &enabled, &name)) {
            return COMMAND_FAILED;
        }
        if (!enabled || !id) {
            return COMMAND_ILLEGAL;
        }
        void *args[] = {unit.id, id, &camera, phase->token};
        if (!save_action(phase) || !task(phase, "manual_character_skill", phase->character_skill,
                                       "ExecuteAsync", 4, args, 0)) {
            return COMMAND_FAILED;
        }
    } else {
        void *skill = 0, *candidates = 0, *targets = 0;
        int enabled, remaining;
        if (!unit_skill_option(phase, &unit, &skill, &enabled, &remaining)) {
            return COMMAND_FAILED;
        }
        if (!enabled) {
            return COMMAND_ILLEGAL;
        }
        if (phase->skill_actor == number && phase->skill_index == -1) {
            targets = phase->selected_skill_targets;
        } else {
            void *find[] = {unit.unit, skill};
            if (!invoke(phase, phase->unit_skill_targets, "FindTargetCandidates", 2, find, &candidates)) {
                return COMMAND_FAILED;
            }
            void *select[] = {skill, candidates};
            if (!invoke(phase, phase->unit_skill_targets, "AutoSelect", 2, select, &targets)) {
                return COMMAND_FAILED;
            }
        }
        void *args[] = {unit.id, targets, &camera, phase->token};
        if (!save_action(phase) || !task(phase, "manual_unit_skill", phase->unit_skill,
                                       "ExecuteAsync", 4, args, 0)) {
            return COMMAND_FAILED;
        }
    }
    printf("UI\tSKILL_USED\t%d\t%d\n", number, index);
    return COMMAND_OK;
}

static int emit_support_attacks(const struct manual_phase *phase, void *choices) {
    void *candidates = 0;
    int count;
    if (!invoke(phase, choices, "Candidates", 0, 0, &candidates) || !list_count(phase, candidates, &count)) {
        return 0;
    }
    for (int i = 0; i < count; ++i) {
        void *candidate = 0, *id = 0;
        long long weapon;
        int number, enabled, damage;
        if (!list_item(phase, candidates, i, &candidate) || !field(phase, candidate, "UnitId", &id) ||
            !get_int(phase, id, "ToInt", &number) || !get_bool(phase, candidate, "get_IsEnabled", &enabled) ||
            !get_int(phase, candidate, "get_HitHpDamage", &damage) ||
            !get_long(phase, candidate, "get_WeaponId", &weapon)) {
            return 0;
        }
        printf("UI\tSUPPORT_ATTACK\t%d\t%d\t%lld\t%d\n", number, enabled, weapon, damage);
    }
    return 1;
}

static int remember_support_plan(struct manual_phase *phase, void *choices, int actor, int target, int weapon) {
    void *candidates = 0;
    int count;
    if (!get(phase, choices, "Candidates", &candidates) || !list_count(phase, candidates, &count) ||
        count > phase->session->unit_count) {
        return 0;
    }
    for (int i = 0; i < count; ++i) {
        struct support_choice *entry = &phase->support_plan[i];
        void *candidate = 0, *id = 0;
        if (!list_item(phase, candidates, i, &candidate) || !field(phase, candidate, "UnitId", &id) ||
            !get_int(phase, id, "ToInt", &entry->number) ||
            !get_bool(phase, candidate, "get_IsEnabled", &entry->enabled) ||
            !get_bool(phase, candidate, "get_IsPreemptiveAttack", &entry->preemptive) ||
            !get_long(phase, candidate, "get_WeaponId", &entry->weapon)) {
            return 0;
        }
    }
    phase->support_count = count;
    phase->support_actor = actor;
    phase->support_target = target;
    phase->support_weapon = weapon;
    return 1;
}

static int restore_support_plan(const struct manual_phase *phase, void *choices) {
    for (int i = 0; i < phase->support_count; ++i) {
        const struct support_choice *entry = &phase->support_plan[i];
        int number = entry->number;
        void *ctor[] = {&number};
        void *id = construct(phase->unit_id_class, 1, ctor), *candidate = 0, *unused = 0;
        void *find[] = {id};
        long long weapon = entry->weapon;
        unsigned char enabled = entry->enabled, preemptive = entry->preemptive;
        void *change[] = {&weapon, &preemptive}, *set[] = {id, &enabled};
        if (!id || !invoke(phase, choices, "FindById", 1, find, &candidate) || !candidate ||
            !invoke(phase, candidate, "ChangeWeapon", 2, change, &unused) ||
            !invoke(phase, choices, "SetEnabled", 2, set, &unused)) {
            return 0;
        }
    }
    return 1;
}

/* PlayerAttackPredictionActionSelectState opens this DTO for the clicked support slot.
   A negative weapon reads the menu, zero selects NonParticipation, and an ID selects the weapon. */
static int select_support_action(const struct manual_phase *phase, void *step, void *choices,
                                 int number, long long requested, int *accepted) {
    *accepted = 0;
    void *ctor[] = {&number};
    void *id = construct(phase->unit_id_class, 1, ctor), *candidate = 0, *unused = 0;
    void *find[] = {id}, *dto = 0, *actions = 0;
    if (!id || !invoke(phase, choices, "FindById", 1, find, &candidate)) {
        return 0;
    }
    if (!candidate) {
        return 1;
    }
    void *create[] = {candidate};
    int count;
    if (!invoke(phase, phase->support_action_list, "_CreateSupportAttackListDto", 1, create, &dto) ||
        !field(phase, dto, "ActionList", &actions) || !list_count(phase, actions, &count)) {
        return 0;
    }
    for (int i = 0; i < count; ++i) {
        void *action = 0, *weapon_list = 0, *cells = 0;
        int cell_count;
        if (!list_item(phase, actions, i, &action)) {
            return 0;
        }
        if (!strcmp(phase->api->class_name(phase->api->object_class(action)), "BattlePredictionNonParticipationDto")) {
            if (requested == 0) {
                unsigned char disabled = 0;
                void *set[] = {id, &disabled};
                *accepted = 1;
                return invoke(phase, choices, "SetEnabled", 2, set, &unused);
            }
            if (requested < 0) {
                printf("UI\tSUPPORT_ACTION\t%d\t0\t1\t\t\n", number);
            }
            continue;
        }
        if (!field(phase, action, "WeaponCellDtoList", &weapon_list) ||
            !field(phase, weapon_list, "IngameUnitWeaponSelectCells", &cells) ||
            !list_count(phase, cells, &cell_count)) {
            return 0;
        }
        for (int j = 0; j < cell_count; ++j) {
            void *cell = 0, *weapon = 0, *reasons = 0;
            long long key;
            int reason_count, map_type;
            if (!list_item(phase, cells, j, &cell) || !field(phase, cell, "AttackWeapon", &weapon) ||
                !get_int(phase, weapon, "get_MapWeaponRangeType", &map_type) ||
                !get_long(phase, weapon, "GetId", &key) ||
                !field(phase, cell, "UnavailableReasons", &reasons) ||
                !list_count(phase, reasons, &reason_count)) {
                return 0;
            }
            if (map_type) {
                continue;
            }
            if (requested < 0) {
                void *name = 0, *reason = 0, *message = 0;
                char label[256], unavailable[256];
                if (reason_count && list_item(phase, reasons, 0, &reason)) {
                    get(phase, reason, "GetShortReason", &message);
                }
                get(phase, weapon, "GetName", &name);
                format_managed_text(name, label, sizeof(label));
                format_managed_text(message, unavailable, sizeof(unavailable));
                printf("UI\tSUPPORT_ACTION\t%d\t%lld\t%d\t%s\t%s\n", number, key, !reason_count,
                       label, unavailable);
                emit_weapon_info(phase, number, key, weapon, 0);
                int power, en;
                if (guarded_field(phase, cell, "Power", &power) &&
                    guarded_field(phase, cell, "EnConsumption", &en)) {
                    printf("UI\tSUPPORT_POWER\t%d\t%lld\t%d\t%d\n", number, key, power, en);
                }
                if (field(phase, cell, "WeaponTraitAdditionDescription", &message)) {
                    emit_weapon_text(number, key, "WEAPON_TEXT", message);
                }
            } else if (key == requested && !reason_count) {
                int is_preemptive;
                if (!invoke(phase, step, "SetEnabledSupportAttack", 1, find, &unused) ||
                    !invoke(phase, choices, "FindEnabled", 1, find, &candidate) || !candidate ||
                    !get_bool(phase, weapon, "IsPreemptiveAttack", &is_preemptive)) {
                    return 0;
                }
                unsigned char preemptive = is_preemptive;
                void *change[] = {&key, &preemptive};
                *accepted = 1;
                return invoke(phase, candidate, "ChangeWeapon", 2, change, &unused);
            }
        }
    }
    *accepted = requested < 0;
    return 1;
}

static int choose_support_attacks(const struct manual_phase *phase, void *choices, const int supporters[3]) {
    void *ids[3] = {0};
    for (int i = 0; i < 3; ++i) {
        if (!supporters[i]) {
            continue;
        }
        int number = supporters[i];
        void *args[] = {&number};
        ids[i] = construct(phase->unit_id_class, 1, args);
        void *find[] = {ids[i]}, *candidate = 0;
        if (!ids[i] || !invoke(phase, choices, "FindById", 1, find, &candidate) || !candidate) {
            return 0;
        }
    }
    unsigned char enabled = 0;
    void *disable[] = {&enabled}, *unused = 0;
    if (!invoke(phase, choices, "SetEnabledAll", 1, disable, &unused)) {
        return 0;
    }
    enabled = 1;
    for (int i = 0; i < 3; ++i) {
        void *args[] = {ids[i], &enabled};
        if (ids[i] && !invoke(phase, choices, "SetEnabled", 2, args, &unused)) {
            return 0;
        }
    }
    return 1;
}

static int refresh_attack_defense(const struct manual_phase *phase, void *step, void *actor, void *target) {
    void *counter = 0, *weapon = 0, *service = 0, *choices = 0, *unused = 0;
    if (!get(phase, step, "get_MainTargetCounterSelection", &counter) ||
        !call(phase->api->object_class(counter), "get_WeaponId", 0, phase->api->unbox(counter), 0, &weapon) ||
        !weapon || !field(phase, phase->prediction, "_supportActionService", &service) || !service) {
        return 0;
    }
    void *args[] = {actor, target, phase->api->unbox(weapon)};
    if (!invoke(phase, service, "SelectAttackSideSupportDefendUnit", 3, args, &choices) || !choices) {
        return 0;
    }
    void *set[] = {choices};
    return invoke(phase, step, "set_AttackSideSupportDefendUnits", 1, set, &unused);
}

static int choose_attack_defense(const struct manual_phase *phase, void *choices, int number, int *accepted) {
    void *id = 0, *unused = 0;
    *accepted = 1;
    if (number > 0) {
        void *args[] = {&number};
        id = construct(phase->unit_id_class, 1, args);
        void *find[] = {id}, *candidate = 0;
        if (!id || !invoke(phase, choices, "FindById", 1, find, &candidate)) {
            return 0;
        }
        if (!candidate) {
            *accepted = 0;
            return 1;
        }
    }
    unsigned char enabled = 1;
    void *args[] = {id, &enabled};
    return invoke(phase, choices, "SetDisabledAll", 0, 0, &unused) &&
        (!id || invoke(phase, choices, "SetEnabled", 2, args, &unused));
}

static int emit_attack_defense(const struct manual_phase *phase, void *choices, int *selected) {
    void *candidates = 0;
    int count;
    *selected = 0;
    if (!invoke(phase, choices, "Candidates", 0, 0, &candidates) || !list_count(phase, candidates, &count)) {
        return 0;
    }
    for (int i = 0; i < count; ++i) {
        void *candidate = 0, *id = 0;
        int number, enabled;
        if (!list_item(phase, candidates, i, &candidate) || !field(phase, candidate, "UnitId", &id) ||
            !get_int(phase, id, "ToInt", &number) ||
            !guarded_bool_field(phase, candidate, "IsEnabled", &enabled)) {
            return 0;
        }
        printf("UI\tATTACK_DEFENSE\t%d\t%d\n", number, enabled);
        if (enabled) {
            *selected = number;
        }
    }
    return 1;
}

static int prediction_damage(const struct manual_phase *phase, void *preview, void *id, int *hp, int *barrier) {
    void *result = 0;
    void *args[] = {id};
    *hp = *barrier = 0;
    return invoke(phase, preview, "GetBattleCalculateResultDtoByIngameUnitId", 1, args, &result) &&
        (!result || (get_int(phase, result, "get_HitHpDamage", hp) &&
                     get_int(phase, result, "get_HitHpBarrierDamage", barrier)));
}

static void emit_defense_energy(const struct manual_phase *phase, int number, void *unit) {
    int current, maximum;
    if (get_int(phase, unit, "get_CurrentEn", &current) && get_int(phase, unit, "GetBuffedMaxEn", &maximum)) {
        printf("UI\tDEFENSE_EN\t%d\t%d\t%d\n", number, current, maximum);
    }
}

static int emit_battle_action(const struct manual_phase *phase, const char *event, void *id,
                              const char *role, void *result, void *origins) {
    void *rate = 0;
    int number, en = 0, hit = -1, hp = 0, barrier = 0;
    long long key = 0;
    char label[256] = "";
    if (!get_int(phase, id, "ToInt", &number)) {
        return 0;
    }
    if (origins) {
        struct unit_info unit;
        if (lookup(phase, origins, number, &unit) && unit.origin) {
            emit_footprint(phase, "DEFENSE_FOOTPRINT", number, &unit);
            emit_defense_energy(phase, number, unit.unit);
        }
    }
    if (result) {
        void *weapon = 0, *name = 0;
        if (field(phase, result, "AttackWeapon", &weapon) && weapon) {
            get_long(phase, weapon, "GetId", &key);
            get(phase, weapon, "GetName", &name);
            format_managed_text(name, label, sizeof(label));
        }
        get_int(phase, result, "get_HitHpDamage", &hp);
        get_int(phase, result, "get_HitHpBarrierDamage", &barrier);
        guarded_field(phase, result, "AttackWeaponEnConsumption", &en);
        if (get(phase, result, "get_HitRate", &rate)) {
            get_int(phase, rate, "get_Value", &hit);
        }
    }
    if (!strcmp(event, "BATTLE_ACTION")) {
        printf("UI\tBATTLE_ORDER\t%d\t%s\t%d\t%d\n", number, role, hit, en);
    }
    printf("UI\t%s\t%d\t%s\t%lld\t%d\t%d\t%d\t%d\t%s\n", event, number, role, key, hp, barrier, hit, en, label);
    return 1;
}

static int emit_order_support(const struct manual_phase *phase, const char *event, void *preview,
                              const char *method, const char *role, void *origins) {
    void *list = 0;
    int count;
    if (!invoke(phase, preview, method, 0, 0, &list) || !list_count(phase, list, &count)) {
        return 0;
    }
    for (int i = 0; i < count; ++i) {
        void *dto = 0, *id = 0, *result = 0;
        if (!list_item(phase, list, i, &dto) || !field(phase, dto, "UnitId", &id) ||
            !field(phase, dto, "CalculateResult", &result) || !emit_battle_action(phase, event, id, role, result, origins)) {
            return 0;
        }
    }
    return 1;
}

static int emit_order_main(const struct manual_phase *phase, const char *event, void *preview, void *id, const char *role) {
    void *result = 0, *args[] = {id};
    return invoke(phase, preview, "GetBattleCalculateResultDtoByIngameUnitId", 1, args, &result) &&
        emit_battle_action(phase, event, id, role, result, 0);
}

static int emit_order_defense(const struct manual_phase *phase, const char *event, void *preview,
                              const char *method, const char *role, void *origins) {
    void *detail = 0, *id = 0;
    return invoke(phase, preview, method, 0, 0, &detail) &&
        (!detail || (field(phase, detail, "IngameUnitId", &id) && emit_battle_action(phase, event, id, role, 0, origins)));
}

/* Same phase ordering as DisplayBattleOrderOfActionUseCase.ExecuteAsync. */
static int emit_battle_order(const struct manual_phase *phase, const char *event, void *preview, void *step,
                             void *actor_id, void *target_id, void *weapon, void *support_origins) {
    static const char *support[2][2] = {
        {"get_LeftPreemptiveSupportAttackList", "get_LeftNormalSupportAttackList"},
        {"get_RightPreemptiveSupportAttackList", "get_RightNormalSupportAttackList"},
    };
    static const char *defend[] = {"GetLeftSupportDefendUnit", "GetRightSupportDefendUnit"};
    int number, left, preemptive = 0, counter_type = 0;
    void *counter = 0, *type = 0, *left_unit = 0, *left_id = 0;
    if (!get_int(phase, actor_id, "ToInt", &number) ||
        !get(phase, preview, "GetLeftBattlePredictionUnitSetDto", &left_unit) ||
        !field(phase, left_unit, "IngameUnitId", &left_id) || !get_int(phase, left_id, "ToInt", &left) ||
        !get_bool(phase, weapon, "IsPreemptiveAttack", &preemptive) ||
        !get(phase, step, "get_MainTargetCounterSelection", &counter) ||
        !call(phase->api->object_class(counter), "get_Type", 0, phase->api->unbox(counter), 0, &type) || !type) {
        return 0;
    }
    int actor_right = left != number;
    counter_type = *(int *) phase->api->unbox(type);
    const char *counter_role[] = {"none", "counter", "evade", "guard", "unavailable"};
    for (int i = 0; i < 2; ++i) {
        if (!emit_order_support(phase, event, preview, support[actor_right][i], "support", support_origins) ||
            (preemptive == !i && (!emit_order_main(phase, event, preview, actor_id, "attack") ||
                                 !emit_order_defense(phase, event, preview, defend[!actor_right], "defense",
                                                     support_origins))) ||
            !emit_order_support(phase, event, preview, support[!actor_right][i], "counter_support", support_origins)) {
            return 0;
        }
    }
    return emit_order_main(phase, event, preview, target_id,
                           (unsigned int) counter_type < 5 ? counter_role[counter_type] : "none") &&
        emit_order_defense(phase, event, preview, defend[actor_right], "attack_defense", support_origins);
}

static int prediction_detail_hp(const struct manual_phase *phase, void *detail, int *hp) {
    void *member = phase->api->get_field(phase->api->object_class(detail), "HpChange");
    void *change = member ? phase->boxed_field(member, detail) : 0;
    return change && guarded_field(phase, change, "Prediction", hp);
}

static int prediction_hp(const struct manual_phase *phase, void *preview, const char *side,
                         int *number, int *hp) {
    void *set = 0, *detail = 0, *id = 0;
    if (!get(phase, preview, side, &set) || !field(phase, set, "IngameUnitId", &id) ||
        !get_int(phase, id, "ToInt", number) || !field(phase, set, "IngameUnitSetDetailDto", &detail) || !detail) {
        return 0;
    }
    return prediction_detail_hp(phase, detail, hp);
}

static void emit_attack_defense_hp(const struct manual_phase *phase, void *preview, int selected) {
    const char *methods[] = {"GetRightSupportDefendUnit", "GetLeftSupportDefendUnit"};
    for (int i = 0; selected && i < 2; ++i) {
        void *detail = 0, *id = 0;
        int number, hp;
        if (invoke(phase, preview, methods[i], 0, 0, &detail) && detail &&
            field(phase, detail, "IngameUnitId", &id) && get_int(phase, id, "ToInt", &number) &&
            number == selected && prediction_detail_hp(phase, detail, &hp)) {
            printf("UI\tATTACK_DEFENSE_HP\t%d\t%d\n", number, hp);
            return;
        }
    }
}

static int emit_damage_detail(const struct manual_phase *phase, const char *event, void *detail, int role,
                              unsigned char *seen) {
    void *id = 0, *member = 0, *change = 0, *barrier = 0;
    int number, current, predicted, damage = 0, shield = 0;
    if (!detail) {
        return 1;
    }
    if (!field(phase, detail, "IngameUnitId", &id) || !get_int(phase, id, "ToInt", &number) ||
        number <= 0 || number > phase->session->unit_count) {
        return 0;
    }
    if (seen[number]) {
        return 1;
    }
    member = phase->api->get_field(phase->api->object_class(detail), "HpChange");
    change = member ? phase->boxed_field(member, detail) : 0;
    if (!change || !guarded_field(phase, change, "Current", &current) ||
        !guarded_field(phase, change, "Prediction", &predicted)) {
        return 0;
    }
    damage = current > predicted ? current - predicted : 0;
    member = phase->api->get_field(phase->api->object_class(change), "Barrier");
    barrier = member ? phase->boxed_field(member, change) : 0;
    if (barrier) {
        int before, after;
        if (guarded_field(phase, barrier, "Current", &before) &&
            guarded_field(phase, barrier, "Prediction", &after)) {
            shield = before - after;
        }
    }
    seen[number] = 1;
    printf("UI\t%s\t%d\t%d\t%d\t%d\t%d\t%d\n", event, number, role, current, predicted, damage, shield);
    return 1;
}

static int emit_prediction_details(const struct manual_phase *phase, const char *event, void *preview) {
    unsigned char seen[phase->session->unit_count + 1];
    for (int i = 0; i <= phase->session->unit_count; ++i) {
        seen[i] = 0;
    }
    const char *defenders[] = {"GetLeftSupportDefendUnit", "GetRightSupportDefendUnit"};
    for (int i = 0; i < 2; ++i) {
        void *detail = 0;
        if (!invoke(phase, preview, defenders[i], 0, 0, &detail) ||
            !emit_damage_detail(phase, event, detail, 1, seen)) {
            return 0;
        }
    }
    const char *phases[] = {"_normalAttack", "_preemptiveAttack"};
    const char *sides[] = {"LeftSide", "RightSide"};
    for (int i = 0; i < 2; ++i) {
        void *attack = 0;
        if (!field(phase, preview, phases[i], &attack) || !attack) {
            continue;
        }
        for (int j = 0; j < 2; ++j) {
            void *side = 0, *detail = 0;
            if (field(phase, attack, sides[j], &side) && side &&
                field(phase, side, "SupportDefendUnitSet", &detail) &&
                !emit_damage_detail(phase, event, detail, 1, seen)) {
                return 0;
            }
        }
    }
    const char *mains[] = {"GetLeftBattlePredictionUnitSetDto", "GetRightBattlePredictionUnitSetDto"};
    for (int i = 0; i < 2; ++i) {
        void *unit = 0, *detail = 0;
        if (!invoke(phase, preview, mains[i], 0, 0, &unit) ||
            (unit && (!field(phase, unit, "IngameUnitSetDetailDto", &detail) ||
                      !emit_damage_detail(phase, event, detail, 0, seen)))) {
            return 0;
        }
    }
    return 1;
}

static int emit_received_damage(const struct manual_phase *phase, const char *event, void *preview) {
    const char *methods[] = {"GetLeftUnitReceivedDamage", "GetRightUnitReceivedDamage"};
    const char *mains[] = {"GetLeftBattlePredictionUnitSetDto", "GetRightBattlePredictionUnitSetDto"};
    const char *defenders[] = {"GetLeftSupportDefendUnit", "GetRightSupportDefendUnit"};
    for (int i = 0; i < 2; ++i) {
        void *actual = 0, *main = 0, *support = 0;
        /* The original summary uses the (main-unit damage, support-defender damage) pair. */
        if (!get(phase, preview, methods[i], &actual) || !field(phase, actual, "Item1", &main) ||
            !field(phase, actual, "Item2", &support)) {
            return 0;
        }
        void *damage[] = {main, support};
        const char *recipients[] = {mains[i], defenders[i]};
        for (int j = 0; j < 2; ++j) {
            void *unit = 0, *id = 0;
            int number, hp = 0, barrier = 0;
            if (!invoke(phase, preview, recipients[j], 0, 0, &unit)) {
                return 0;
            }
            if (unit && field(phase, unit, "IngameUnitId", &id) && get_int(phase, id, "ToInt", &number)) {
                if (damage[j] && (!guarded_field(phase, damage[j], "HpDamage", &hp) ||
                                  !guarded_field(phase, damage[j], "HpBarrierDamage", &barrier))) {
                    continue;
                }
                printf("UI\t%s\t%d\t%d\t%d\n", event, number, hp, barrier);
            }
        }
    }
    return 1;
}

static int read_save_data(const struct manual_phase *phase, void **save) {
    void *loaded = 0;
    void *args[] = {save};
    return invoke(phase, phase->exchange->memento, "_TryLoadAllMemoryPackSaveData", 1, args, &loaded) &&
        loaded && *(unsigned char *) phase->api->unbox(loaded) && *save;
}

static int read_snapshot(const struct manual_phase *phase, void **snapshot) {
    void *saved = 0;
    return read_save_data(phase, &saved) && field(phase, saved, "LatestStep", snapshot) && *snapshot;
}

static int restore_snapshot(const struct manual_phase *phase, void *snapshot, const char *label) {
    void *result = 0, *turn = 0, *key = 0, *random = 0, *unused = 0;
    void *args[] = {snapshot, phase->token};
    if (!task(phase, label, phase->exchange->memento, "LoadStepSnapshotAsync", 2, args, &result) ||
        !result || !*(unsigned char *) phase->api->unbox(result) ||
        !get(phase, phase->session->turn_repository, "FindLatest", &turn) ||
        !get(phase, turn, "GetCurrentTurnPhaseStepKey", &key) ||
        !get(phase, phase->session->random_repository, "Get", &random)) {
        return 0;
    }
    void *random_args[] = {key};
    return invoke(phase, random, "InitStep", 1, random_args, &unused);
}

static int check_mumu_supporters(const struct manual_phase *phase, void *snapshot) {
    void *repository = phase->session->supporter_repository;
    void *saved = 0, *rows = 0, *live = 0;
    int row_count, live_count;
    if (!field(phase, snapshot, "SupporterList", &saved) || !field(phase, saved, "Values", &rows) ||
        !get(phase, repository, "FindAll", &live) || !list_count(phase, rows, &row_count) ||
        !list_count(phase, live, &live_count) || row_count != live_count) {
        return 0;
    }
    /* The memento looks up supporters by the instance ID from the original sortie. */
    for (int i = 0; i < row_count; ++i) {
        void *row = 0, *group = 0, *supporter = 0, *master = 0, *master_id = 0, *id = 0;
        long long saved_id, live_id, saved_master, live_master, saved_exp, live_exp;
        int saved_stars, live_stars;
        if (!list_item(phase, rows, i, &row) || !field(phase, row, "IngameUnitGroup", &group) || !group) {
            printf("REUSE mumu_saved_supporter_missing %d\n", i);
            return 0;
        }
        void *group_args[] = {group};
        if (!invoke(phase, repository, "FindByIngameUnitGroup", 1, group_args, &supporter) || !supporter ||
            !get(phase, supporter, "get_MasterSupporter", &master) || !get(phase, master, "get_Id", &master_id) ||
            !get_long(phase, master_id, "ToLong", &live_master) || !get(phase, supporter, "get_Id", &id) ||
            !get_long(phase, id, "ToLong", &live_id)) {
            printf("REUSE mumu_supporter_lookup_failed %d\n", i);
            return 0;
        }
        if (!guarded_key(phase, row, "IngameSupporterId", &saved_id) ||
            !guarded_key(phase, row, "MasterSupporterId", &saved_master) || !guarded_key(phase, row, "Exp", &saved_exp) ||
            !guarded_field(phase, row, "LimitBreakStep", &saved_stars) ||
            !get_long(phase, supporter, "get_Exp", &live_exp)) {
            printf("REUSE mumu_supporter_stats_failed %d\n", i);
            return 0;
        }
        void *stars = 0;
        void *raw_stars = 0;
        if (!get(phase, supporter, "get_LimitBreakStep", &stars) || !get(phase, stars, "get_Value", &raw_stars)) {
            return 0;
        }
        live_stars = *(short *) phase->api->unbox(raw_stars);
        if (saved_id != live_id || saved_master != live_master || live_exp != saved_exp || live_stars != saved_stars) {
            printf("REUSE mumu_supporter_mismatch %d master %lld/%lld exp %lld/%lld stars %d/%d\n", i,
                   saved_master, live_master, saved_exp, live_exp, saved_stars, live_stars);
            return 0;
        }
    }
    return 1;
}

static int mumu_random_key(const struct manual_phase *phase, unsigned long long key[3]) {
    void *turn = 0, *key_object = 0, *parts = 0;
    if (!get(phase, phase->session->turn_repository, "FindLatest", &turn) ||
        !get(phase, turn, "GetCurrentTurnPhaseStepKey", &key_object) ||
        !get(phase, key_object, "ToUlongArray", &parts) || phase->array_length(parts) != 3) {
        return 0;
    }
    memcpy(key, (const char *) parts + phase->array_header(), 3 * sizeof(*key));
    return 1;
}

static int import_mumu_random(const struct manual_phase *phase, void *file) {
    struct mumu_random_state data;
    unsigned char extra;
    int valid = fread(&data, sizeof(data), 1, file) == 1 && !fread(&extra, 1, 1, file) && !ferror(file);
    fclose(file);
    if (!valid || memcmp(data.magic, "GGETRNG\1", 8) || data.reserved) {
        return 0;
    }
    unsigned long long key[3];
    if (!mumu_random_key(phase, key)) {
        return 0;
    }
    if (memcmp(key, data.key, sizeof(key))) {
        printf("REUSE mumu_random_key_mismatch expected %llu/%llu/%llu got %llu/%llu/%llu\n",
               key[0], key[1], key[2], data.key[0], data.key[1], data.key[2]);
        return 0;
    }
    for (int i = 0; i < 3; ++i) {
        if (data.random.streams[i].cursor < 0 || data.random.streams[i].cursor > MERSENNE_STATE_WORDS) {
            return 0;
        }
    }
    void *turn = 0, *step_key = 0, *unused = 0;
    void *seed_args[] = {&data.stage_seed};
    void *rng = construct(phase->random_class, 1, seed_args);
    void *undo_args[] = {&data.undo_count};
    void *repo_args[] = {rng};
    if (!rng || !invoke(phase, rng, "set_UndoCount", 1, undo_args, &unused) ||
        !get(phase, phase->session->turn_repository, "FindLatest", &turn) ||
        !get(phase, turn, "GetCurrentTurnPhaseStepKey", &step_key)) {
        return 0;
    }
    void *init_args[] = {step_key};
    struct forecast_random state;
    if (!invoke(phase, rng, "InitStep", 1, init_args, &unused) ||
        !invoke(phase, phase->session->random_repository, "Save", 1, repo_args, &unused) ||
        !forecast_random_state(phase, &state, 0)) {
        return 0;
    }
    state.state = data.random;
    return forecast_random_state(phase, &state, 1);
}

static int import_mumu_checkpoint(struct manual_phase *phase, const struct stage_phase_services *phases) {
    void *source = fopen(battle_path(MUMU_CHECKPOINT), "rb");
    if (!source) {
        return 0;
    }
    void *destination = fopen(battle_path(BATTLE_CHECKPOINT), "wb");
    unsigned char chunk[4096];
    unsigned long size, total = 0;
    int copied = destination != 0;
    while (copied && (size = fread(chunk, 1, sizeof(chunk), source)) != 0) {
        total += size;
        copied = total <= CHECKPOINT_MAX_SIZE && fwrite(chunk, 1, size, destination) == size;
    }
    copied = copied && !ferror(source);
    fclose(source);
    if (!destination || fclose(destination) || !copied || !total) {
        return 0;
    }
    void *save = 0, *stage_id = 0, *snapshot = 0, *turn = 0, *map = 0, *origins = 0;
    long long stage;
    if (!read_save_data(phase, &save) || !field(phase, save, "StageId", &stage_id) ||
        !get_long(phase, stage_id, "ToLong", &stage) || stage != phase->session->stage->id ||
        !field(phase, save, "LatestStep", &snapshot) || !snapshot ||
        !check_mumu_supporters(phase, snapshot) ||
        !restore_snapshot(phase, snapshot, "mumu_checkpoint_restore") ||
        !get(phase, phase->session->turn_repository, "FindLatest", &turn) ||
        !get_int(phase, turn, "get_Number", &phase->turn) ||
        !get_int(phase, turn, "get_CurrentPhaseBattleSide", &phase->side) || phase->side != 0 ||
        !map_origins(phase, &map, &origins) || !read_board_size(phase, map)) {
        return 0;
    }
    void *step = 0;
    int first_step = 0;
    if (!get(phase, turn, "GetCurrentStep", &step) || !get_bool(phase, step, "IsFirstStep", &first_step)) {
        return 0;
    }
    void *random_file = fopen(battle_path(MUMU_RANDOM), "rb");
    if (random_file && !import_mumu_random(phase, random_file)) {
        printf("REUSE mumu_random_state_invalid\n");
        return 0;
    }
    printf("REUSE mumu_random_state %s\n", random_file ? "imported" : "local");
    /* The original client saves before phase-start recovery. Mid-phase saves
       already contain the resulting HP, EN and traits. */
    if (first_step && !start_player_phase(phase->session, phases, phase->api, phase->loop, phase->token,
                                         phase->turn, phase->side)) {
        return 0;
    }
    printf("UI\tLOADED\t%d\t0\n", phase->turn);
    return 1;
}

static int forecast_random_state(const struct manual_phase *phase, struct forecast_random *state, int restore) {
    const char *fields[] = {"<AIRandom>k__BackingField", "<BattleRandom>k__BackingField", "<GeneralRandom>k__BackingField"};
    const char *setters[] = {"set_AIRandom", "set_BattleRandom", "set_GeneralRandom"};
    void *entity = 0;
    if (!get(phase, phase->session->random_repository, "Get", &entity)) {
        return 0;
    }
    for (int i = 0; i < 3; ++i) {
        if (restore) {
            void *args[] = {state->streams[i].stream}, *unused = 0;
            if (!invoke(phase, entity, setters[i], 1, args, &unused)) {
                return 0;
            }
            phase->set_field(state->streams[i].twister, state->streams[i].cursor_field, &state->state.streams[i].cursor);
            memcpy((char *) state->streams[i].array + phase->array_header(),
                   state->state.streams[i].words, sizeof(state->state.streams[i].words));
        } else {
            void *stream = 0, *twister = 0, *array = 0;
            if (!field(phase, entity, fields[i], &stream) || !field(phase, stream, "_mt", &twister) ||
                !field(phase, twister, "mt", &array) || !array || phase->array_length(array) != MERSENNE_STATE_WORDS) {
                return 0;
            }
            void *cursor = phase->api->get_field(phase->api->object_class(twister), "mti");
            if (!cursor) {
                return 0;
            }
            state->streams[i].stream = stream;
            state->streams[i].twister = twister;
            state->streams[i].array = array;
            state->streams[i].cursor_field = cursor;
            phase->api->get_field_value(twister, cursor, &state->state.streams[i].cursor);
            memcpy(state->state.streams[i].words, (char *) array + phase->array_header(),
                   sizeof(state->state.streams[i].words));
        }
    }
    snapshot_unity_random(state->state.unity, restore);
    return 1;
}

static int discard_attack_review(struct manual_phase *phase) {
    struct attack_review *review = &phase->review;
    if (!review->snapshot) {
        return 1;
    }
    long long started = wallclock_milliseconds();
    phase->moved_unit = review->moved_unit;
    int ok = restore_snapshot(phase, review->snapshot, "manual_forecast_restore") &&
        forecast_random_state(phase, &review->random, 1);
    review->snapshot = review->result = 0;
    printf("REUSE manual_forecast_discard_ms %lld\n", wallclock_milliseconds() - started);
    return ok;
}

static int review_matches_command(const struct attack_review *review, const char *line) {
    char command;
    int actor, target, weapon;
    return sscanf(line, "%c %d %d %d", &command, &actor, &target, &weapon) == 4 &&
        (command == 'p' || command == 'a' || command == 'b') &&
        review->actor == actor && review->target == target && review->weapon == weapon &&
        (!review->closed || (command == 'p' && !review->modified));
}

static int select_overload(const struct manual_phase *phase, long long key, int keep, void *coord) {
    /* The two Execute overloads take different value types and share an arity. */
    unsigned char preserve = keep != 0;
    void *args[] = {&key, coord ? phase->api->unbox(coord) : &preserve}, *error = 0;
    phase->runtime_invoke(coord ? phase->map_select_tap : phase->map_select_open, phase->map_select, args, &error);
    if (error) {
        printf("REUSE manual_map_select_error %s\n", phase->api->class_name(phase->api->object_class(error)));
    }
    return !error;
}

static int bind_map_selection(void *library, struct manual_phase *phase) {
    const void *(*methods)(void *, void **) = dlsym(library, "il2cpp_class_get_methods");
    const char *(*method_name)(const void *) = dlsym(library, "il2cpp_method_get_name");
    unsigned int (*parameters)(const void *) = dlsym(library, "il2cpp_method_get_param_count");
    const char *(*parameter)(const void *, unsigned int) = dlsym(library, "il2cpp_method_get_param_name");
    phase->runtime_invoke = dlsym(library, "il2cpp_runtime_invoke");
    if (!methods || !method_name || !parameters || !parameter || !phase->runtime_invoke) {
        return 0;
    }
    void *iterator = 0, *klass = phase->api->object_class(phase->map_select);
    const void *method;
    while ((method = methods(klass, &iterator))) {
        if (!strcmp(method_name(method), "Execute") && parameters(method) == 2) {
            const char *name = parameter(method, 1);
            if (name && !strcmp(name, "selectedCoord")) {
                phase->map_select_tap = method;
            } else if (name && !strcmp(name, "keepSelection")) {
                phase->map_select_open = method;
            }
        }
    }
    return phase->map_select_open && phase->map_select_tap;
}

static int map_can_fire(const struct manual_phase *phase, const struct unit_info *unit,
                        void *weapon, void *service, void *coords, int *allowed) {
    void *reasons = 0, *targets = 0;
    int reason_count, target_count;
    void *args[] = {unit->unit, weapon};
    if (!invoke(phase, service, "GetUnavailableReasonsAfterCheck", 2, args, &reasons) ||
        !list_count(phase, reasons, &reason_count)) {
        return 0;
    }
    *allowed = 0;
    if (reason_count || !coords || !phase->array_length(coords)) {
        return 1;
    }
    /* The original selection screen also disables confirmation with no targets. */
    void *target_args[] = {unit->unit, weapon, coords};
    if (!invoke(phase, service, "GetTargetList", 3, target_args, &targets)) {
        return 0;
    }
    if (targets) {
        if (!list_count(phase, targets, &target_count)) {
            return 0;
        }
        *allowed = target_count > 0;
    }
    return 1;
}

static int emit_map_preview(const struct manual_phase *phase, const struct unit_info *unit,
                            void *weapon, void *service, int type, void *coords, int direction,
                            const struct map_gauge *gauges) {
    void *slots = 0, *dto = 0, *range = 0;
    unsigned char first = 0;
    if (!field(phase, phase->map_select, "_selectedCoordSlots", &slots) || !slots) {
        return 0;
    }
    void *args[] = {unit->unit, weapon, slots, &first, 0};
    if (!invoke(phase, service, "CreateMapWeaponEffectRangeDto", 5, args, &dto) || !dto ||
        !invoke(phase, dto, "get_AttackableCoords", 0, 0, &range)) {
        return 0;
    }
    if (type == 2) {
        if (!invoke(phase, dto, "get_ImpactRangeCoords", 0, 0, &range)) {
            return 0;
        }
    }
    int count = coords ? (int) phase->array_length(coords) : 0;
    int fire;
    if (!map_can_fire(phase, unit, weapon, service, coords, &fire)) {
        return 0;
    }
    printf("UI\tMAP_PREVIEW\t%d\t%d\t%d\t%d\n", type, direction, count, fire);
    if (fire) {
        for (int i = 0; i < phase->session->unit_count; ++i) {
            if (gauges[i].valid) {
                printf("UI\tMAP_HP\t%d\t%d\t%d\n", i + 1, gauges[i].current, gauges[i].predicted);
            }
        }
    }
    emit_map_target_ids(phase, dto);
    if (!emit_map_coords(phase, range, "MAP_EFFECT", -1)) {
        return 0;
    }
    if (type != 2) {
        return 1;
    }
    void *overlap = 0;
    return invoke(phase, dto, "get_OverlapCoords", 0, 0, &overlap) &&
        emit_map_coords(phase, overlap, "MAP_OVERLAP", -1) && emit_map_selectable(phase, unit, weapon, service) &&
        (phase->array_length(slots) > 1 ? emit_map_slots(phase, unit, weapon, service, slots, coords)
                                       : emit_selected_map_coords(phase, coords));
}

static int map_remaining_ammo(const struct manual_phase *phase, void *unit, void *weapon, int *ammo) {
    void *args[] = {weapon}, *value = 0;
    if (!invoke(phase, unit, "GetMapWeaponRemainingAmmo", 1, args, &value) || !value) {
        return 0;
    }
    *ammo = *(int *) phase->api->unbox(value);
    return 1;
}

static enum command_result map_weapon_action(struct manual_phase *phase, void *origins, int number,
                                            long long key, char mode, int a, int b) {
    struct unit_info actor;
    if (!lookup(phase, origins, number, &actor)) {
        return COMMAND_FAILED;
    }
    if (!can_operate(phase, &actor)) {
        return COMMAND_ILLEGAL;
    }
    if (mode != 'o' && (phase->map_actor != number || phase->map_weapon_id != key)) {
        return COMMAND_ILLEGAL;
    }
    void *weapon = 0, *service = 0;
    void *find[] = {&key};
    int type, allowed;
    if (!invoke(phase, actor.unit, "GetUnitMapWeapon", 1, find, &weapon)) {
        return COMMAND_FAILED;
    }
    if (!weapon) {
        return COMMAND_ILLEGAL;
    }
    if (!map_weapon_service(phase, weapon, &type, &service) ||
        !map_weapon_allowed(phase, &actor, number, weapon, service, &allowed)) {
        return COMMAND_FAILED;
    }
    if (!allowed) {
        return COMMAND_ILLEGAL;
    }
    if (mode != 'o' && mode != 't' && mode != 'r' && mode != 's' && mode != 'f') {
        return COMMAND_ILLEGAL;
    }
    int directional = type == 3 || type == 4;
    if ((mode == 'r' && (!directional || a < 1 || a > 4)) ||
        ((mode == 't' || mode == 's') && type != 2)) {
        return COMMAND_ILLEGAL;
    }
    if (mode == 's') {
        int slots;
        if (!get_int(phase, weapon, "get_MapWeaponImpactPointMaxSelectCount", &slots)) {
            return COMMAND_FAILED;
        }
        if (a < 0 || a >= slots) {
            return COMMAND_ILLEGAL;
        }
    }
    void *snapshot = 0, *unused = 0, *coords = 0;
    int moved = phase->moved_unit;
    struct forecast_random random;
    if (!forecast_random_state(phase, &random, 0) || !write_snapshot(phase) || !read_snapshot(phase, &snapshot)) {
        return COMMAND_FAILED;
    }
    int same = mode != 'o';
    int direction = same ? phase->map_direction : 0;
    int rejected_tap = 0;
    struct map_gauge gauges[phase->session->unit_count];
    map_gauges.values = gauges;
    map_gauges.count = phase->session->unit_count;
    map_gauges.phase = mode == 'f' ? 0 : phase;
    reset_map_gauges(0, 0);
    /* Each original selection publishes the HP changes for its final set of targets. */
    int ok = prepare_action(phase, origins, number, actor.unit) && select_overload(phase, key, same, 0);
    if (ok && mode == 't') {
        void *coord = new_coord(phase, (struct coord) {a, b});
        void *tap[] = {actor.unit, weapon, coord ? phase->api->unbox(coord) : 0}, *valid = 0;
        ok = coord && invoke(phase, service, "CanTapCell", 3, tap, &valid) && valid;
        if (ok) {
            rejected_tap = !*(unsigned char *) phase->api->unbox(valid);
            if (!rejected_tap) {
                ok = select_overload(phase, key, 0, coord);
            }
        }
    } else if (ok && mode == 's') {
        void *slot[] = {&a};
        ok = invoke(phase, phase->map_select, "ToggleOrSwitchSlot", 1, slot, &unused);
    }
    if (ok && directional) {
        if (mode == 'r') {
            direction = a;
        }
        if (direction > 0 && direction <= 4) {
            void *rotate[] = {&number, &key, &direction};
            ok = invoke(phase, phase->map_select, "_OnSelectDirectionHandler", 3, rotate, &unused);
        } else {
            ok = field(phase, phase->map_select, "_selectedDirection", &direction);
        }
    }
    map_gauges.phase = 0;
    if (!ok) {
        printf("REUSE manual_map_selection_failed %d %lld %c\n", number, key, mode);
    }
    if (ok) {
        ok = invoke(phase, phase->map_select, "get_SelectedCoords", 0, 0, &coords) && coords;
    }
    if (ok && mode == 'f') {
        int fire;
        ok = map_can_fire(phase, &actor, weapon, service, coords, &fire);
        if (ok && fire) {
            /* Capture the original pre-selection state in the existing undo path. */
            if (!restore_snapshot(phase, snapshot, "manual_map_restore") ||
                !forecast_random_state(phase, &random, 1)) {
                return COMMAND_FAILED;
            }
            phase->moved_unit = moved;
            if (!map_origins(phase, &unused, &origins) || !lookup(phase, origins, number, &actor) ||
                !start_action(phase, origins, number, actor.unit)) {
                return COMMAND_FAILED;
            }
            if (directional && direction) {
                void *turn[] = {actor.unit, &direction};
                if (!invoke(phase, phase->map_select, "_TurnAroundIfNeeded", 2, turn, &unused)) {
                    return COMMAND_FAILED;
                }
            }
            void *fire[] = {&key, coords, phase->token};
            int ammo_before, ammo_after;
            if (!map_remaining_ammo(phase, actor.unit, weapon, &ammo_before)) {
                return COMMAND_FAILED;
            }
            tracing_manual_map = 1;
            int fired = task(phase, "manual_map_weapon", phase->map_execute, "ExecuteAsync", 3, fire, 0);
            tracing_manual_map = 0;
            if (!fired || !map_remaining_ammo(phase, actor.unit, weapon, &ammo_after)) {
                return COMMAND_FAILED;
            }
            printf("REUSE manual_map_ammo unit %d weapon %lld %d->%d\n", number, key, ammo_before, ammo_after);
            phase->moved_unit = phase->map_actor = 0;
            printf("UI\tMAP_USED\t%d\t%lld\n", number, key);
            return COMMAND_OK;
        }
    } else if (ok && !rejected_tap) {
        ok = emit_map_preview(phase, &actor, weapon, service, type, coords, direction, gauges);
        if (!ok) {
            printf("REUSE manual_map_preview_failed %d %lld\n", number, key);
        }
    }
    phase->moved_unit = moved;
    if (!restore_snapshot(phase, snapshot, "manual_map_restore") || !forecast_random_state(phase, &random, 1) || !ok) {
        return COMMAND_FAILED;
    }
    phase->map_actor = number;
    phase->map_weapon_id = key;
    phase->map_direction = direction;
    return mode == 'f' || rejected_tap ? COMMAND_ILLEGAL : COMMAND_OK;
}

static int emit_weapon_estimate(const struct manual_phase *phase, const struct unit_info *actor,
                                const struct unit_info *target, int number, int target_number,
                                int weapon_index, long long weapon_id) {
    /* AttackSelectState._PredictAttack brackets a single attack with per-unit prediction state. */
    void *counter_selection = phase->new_object(phase->counter_class);
    if (!counter_selection) {
        return 0;
    }
    void *unused = 0;
    if (!invoke(phase, actor->unit, "BeginBattlePrediction", 0, 0, &unused)) {
        return 0;
    }
    int target_prepared = invoke(phase, target->unit, "BeginBattlePrediction", 0, 0, &unused);
    void *participants_args[] = {actor->unit, target->unit, 0, 0, 0, 0, phase->api->unbox(counter_selection)};
    void *participants = target_prepared ? construct(phase->participation_class, 7, participants_args) : 0;
    int counter_type = 0;
    unsigned char keep_weapon_traits = 0;
    void *args[] = {actor->unit, phase->api->unbox(actor->origin), &weapon_id, target->unit,
                    &counter_type, 0, participants, &keep_weapon_traits};
    void *result = 0, *rate = 0;
    int damage, barrier, hit_rate, power, en;
    int calculated = participants && invoke(phase, phase->calculator, "PredictAttack", 8, args, &result) &&
        get_int(phase, result, "get_HitHpDamage", &damage) &&
        get_int(phase, result, "get_HitHpBarrierDamage", &barrier) &&
        get(phase, result, "get_HitRate", &rate) && get_int(phase, rate, "get_Value", &hit_rate);
    int actor_restored = invoke(phase, actor->unit, "EndBattlePrediction", 0, 0, &unused);
    int target_restored = invoke(phase, target->unit, "EndBattlePrediction", 0, 0, &unused);
    if (!calculated || !actor_restored || !target_restored) {
        return 0;
    }
    printf("UI\tWEAPON_ESTIMATE\t%d\t%d\t%d\t%d\t%d\t%d\n", number, target_number, weapon_index,
           damage, barrier, hit_rate);
    if (guarded_field(phase, result, "AttackWeaponPower", &power) &&
        guarded_field(phase, result, "AttackWeaponEnConsumption", &en)) {
        printf("UI\tWEAPON_POWER\t%d\t%d\t%d\t%d\t%d\n", number, target_number, weapon_index, power, en);
    }
    return 1;
}

static enum command_result estimate_target(const struct manual_phase *phase, void *origins, int actor_number,
                                           int target_number, int selected) {
    struct unit_info actor, target;
    if (!lookup(phase, origins, actor_number, &actor) || !lookup(phase, origins, target_number, &target)) {
        return COMMAND_FAILED;
    }
    if (!can_operate(phase, &actor) || !hostile(phase, &actor, &target)) {
        return COMMAND_ILLEGAL;
    }
    void *weapons = 0;
    int count;
    if (!weapon_list(phase, &actor, &target, &weapons, &count)) {
        return COMMAND_FAILED;
    }
    if (selected < 0 || selected >= count) {
        return COMMAND_ILLEGAL;
    }
    long long started = wallclock_milliseconds();
    for (int i = 0; i < count; ++i) {
        int index = (selected + i) % count;
        void *weapon = 0;
        long long id;
        if (!weapon_at(phase, weapons, index, &weapon, &id) ||
            !emit_weapon_estimate(phase, &actor, &target, actor_number, target_number, index, id)) {
            return COMMAND_FAILED;
        }
        fflush(0);
    }
    printf("REUSE manual_estimates_ms %d %lld\n", count, wallclock_milliseconds() - started);
    return COMMAND_OK;
}

enum attack_mode { WEAPON_ESTIMATE, BATTLE_PREVIEW, EXECUTE_ATTACK };

static enum command_result attack_unit(struct manual_phase *phase, void *origins, int number,
                                       int target_number, int weapon_index, const int supporters[3],
                                       int defender, enum attack_mode mode,
                                       int support_unit, long long support_weapon) {
    struct unit_info actor, target;
    if (!lookup(phase, origins, number, &actor) || !lookup(phase, origins, target_number, &target)) {
        return COMMAND_FAILED;
    }
    if (!can_operate(phase, &actor) || !hostile(phase, &actor, &target)) {
        return COMMAND_ILLEGAL;
    }
    void *weapons = 0;
    int count;
    if (!weapon_list(phase, &actor, &target, &weapons, &count)) {
        return COMMAND_FAILED;
    }
    if (weapon_index < 0 || weapon_index >= count) {
        return COMMAND_ILLEGAL;
    }
    void *weapon = 0;
    long long weapon_id;
    if (!weapon_at(phase, weapons, weapon_index, &weapon, &weapon_id)) {
        return COMMAND_FAILED;
    }
    if (mode == WEAPON_ESTIMATE) {
        return emit_weapon_estimate(phase, &actor, &target, number, target_number, weapon_index, weapon_id)
            ? COMMAND_OK : COMMAND_FAILED;
    }
    int forecast = mode == BATTLE_PREVIEW;
    int use_plan = supporters[0] == -2;
    if (use_plan && (phase->support_actor != number || phase->support_target != target_number ||
                     phase->support_weapon != weapon_index)) {
        return COMMAND_ILLEGAL;
    }
    struct attack_review *review = &phase->review;
    int continuing = review->snapshot && review->result;
    unsigned char auto_support = !continuing;
    void *prediction_args[] = {actor.unit, target.unit, &weapon_id, phase->api->unbox(actor.origin), &auto_support};
    void *preview = review->result, *step = 0, *choices = 0, *defense_choices = 0;
    if (!continuing) {
        long long started = wallclock_milliseconds();
        if (forecast) {
            if (!forecast_random_state(phase, &review->random, 0) ||
                !checkpoint_morale(phase, review->morale, 0) ||
                !write_snapshot(phase) || !read_snapshot(phase, &review->snapshot)) {
                return COMMAND_FAILED;
            }
            review->moved_unit = phase->moved_unit;
            review->actor = number;
            review->target = target_number;
            review->weapon = weapon_index;
            review->closed = 0;
            review->modified = 0;
        }
        long long snapshotted = wallclock_milliseconds();
        if (!(forecast ? prepare_action(phase, origins, number, actor.unit)
                       : start_action(phase, origins, number, actor.unit))) {
            return COMMAND_FAILED;
        }
        long long prepared = wallclock_milliseconds();
        if (!invoke(phase, phase->prediction, "Execute", 5, prediction_args, &preview) || !preview) {
            return COMMAND_FAILED;
        }
        if (forecast) {
            printf("REUSE manual_forecast_ms snapshot %lld step %lld predict %lld\n",
                   snapshotted - started, prepared - snapshotted, wallclock_milliseconds() - prepared);
        }
    }
    int ok = current_step(phase, &step) && get(phase, step, "get_SupportAttackUnits", &choices);
    int refresh = 0;
    int explicit_supporters = supporters[0] >= 0 &&
        (!continuing || memcmp(review->supporters, supporters, sizeof(review->supporters)));
    if (ok && ((use_plan && !continuing) || explicit_supporters)) {
        auto_support = 0;
        ok = (explicit_supporters ? choose_support_attacks(phase, choices, supporters)
                                  : restore_support_plan(phase, choices)) &&
            invoke(phase, phase->prediction, "Execute", 5, prediction_args, &preview) && preview &&
            refresh_attack_defense(phase, step, actor.unit, target.unit);
        refresh = 1;
    }
    int accepted = 1;
    if (ok && defender >= 0 && (!continuing || review->defender != defender)) {
        ok = get(phase, step, "get_AttackSideSupportDefendUnits", &defense_choices) &&
            choose_attack_defense(phase, defense_choices, defender, &accepted);
        refresh = 1;
    }
    if (ok && support_unit) {
        int selected = 0;
        if (!select_support_action(phase, step, choices, support_unit, support_weapon, &selected)) {
            return COMMAND_FAILED;
        }
        if (!selected || support_weapon < 0) {
            return selected ? COMMAND_OK : COMMAND_ILLEGAL;
        }
        refresh = 1;
    }
    if (ok && refresh) {
        auto_support = 0;
        ok = invoke(phase, phase->prediction, "Execute", 5, prediction_args, &preview) && preview;
    }
    if (forecast) {
        int damage, barrier, counter, counter_barrier;
        if (ok) {
            ok = prediction_damage(phase, preview, actor.id, &damage, &barrier) &&
                prediction_damage(phase, preview, target.id, &counter, &counter_barrier);
        }
        if (ok) {
            int left = 0, right = 0, left_hp = -1, right_hp = -1, actor_hp = -1, target_hp = -1;
            if (prediction_hp(phase, preview, "GetLeftBattlePredictionUnitSetDto", &left, &left_hp) &&
                prediction_hp(phase, preview, "GetRightBattlePredictionUnitSetDto", &right, &right_hp)) {
                actor_hp = number == left ? left_hp : number == right ? right_hp : -1;
                target_hp = target_number == left ? left_hp : target_number == right ? right_hp : -1;
            }
            printf("UI\tPREVIEW\t%d\t%d\t%d\t%d\t%d\t%d\t%d\t%d\t%d\n",
                   number, target_number, weapon_index, damage, counter, barrier, counter_barrier, actor_hp, target_hp);
            int selected;
            ok = get(phase, step, "get_SupportAttackUnits", &choices) && emit_support_attacks(phase, choices) &&
                remember_support_plan(phase, choices, number, target_number, weapon_index) &&
                get(phase, step, "get_AttackSideSupportDefendUnits", &defense_choices) &&
                emit_attack_defense(phase, defense_choices, &selected);
            if (ok) {
                if (support_unit) {
                    printf("UI\tSUPPORT_CHANGED\n");
                }
                if (!accepted) {
                    printf("UI\tATTACK_DEFENSE_RESET\n");
                }
                emit_attack_defense_hp(phase, preview, selected);
                ok = emit_prediction_details(phase, "DAMAGE_DETAIL", preview);
                emit_received_damage(phase, "BATTLE_RECEIVED", preview);
                if (!emit_battle_order(phase, "BATTLE_ACTION", preview, step, actor.id, target.id, weapon, 0)) {
                    printf("REUSE manual_battle_order_unavailable\n");
                }
            }
        }
        if (ok) {
            review->result = preview;
            review->closed = 0;
            review->modified |= refresh;
            if (!continuing || defender >= 0) {
                review->defender = accepted ? defender : -1;
            }
            if (!continuing || supporters[0] >= 0) {
                memcpy(review->supporters, supporters, sizeof(review->supporters));
            }
        }
        return ok ? COMMAND_OK : COMMAND_FAILED;
    }
    if (!ok) {
        return COMMAND_FAILED;
    }
    if (continuing) {
        /* The first preview saved the pre-attack state and prepared the original step. */
        if (!push_checkpoint(phase, capture_checkpoint(phase, review->moved_unit, review))) {
            return COMMAND_FAILED;
        }
        review->snapshot = review->result = 0;
    }
    printf("REUSE manual_attack_predicted\n");
    if (defender >= 0) {
        printf("REUSE manual_attack_defense_selected %d accepted %d\n", defender, accepted);
    }
    fflush(0);
    void *attack_args[] = {actor.unit, target.unit, &weapon_id, phase->token};
    if (!task(phase, "manual_attack", phase->attack, "ExecuteAsync", 4, attack_args, 0)) {
        return COMMAND_FAILED;
    }
    phase->moved_unit = 0;
    printf("REUSE manual_attack turn %d unit %d target %d weapon %lld\n", phase->turn, number, target_number, weapon_id);
    return COMMAND_OK;
}

static int restore_checkpoint(struct manual_phase *phase, const struct battle_checkpoint *entry) {
    /* The original MemoryPack reader also reinstalls the memento's action and skill records. */
    char pending[BATTLE_PATH_CAPACITY];
    build_battle_path(pending, BATTLE_CHECKPOINT ".undo");
    if (!write_snapshot(phase)) {
        return 0;
    }
    void *file = fopen(pending, "wb");
    if (!file) {
        return 0;
    }
    unsigned long prefix = entry->header.unit_count * sizeof(struct save_unit_mp);
    int written = fwrite(entry->bytes + prefix, 1, entry->header.size, file) == entry->header.size;
    if (fclose(file) || !written || rename(pending, battle_path(BATTLE_CHECKPOINT))) {
        return 0;
    }
    unsigned long long seed = entry->header.stage_seed, undo_count = entry->header.undo_count;
    void *random_args[] = {&seed};
    void *random = construct(phase->random_class, 1, random_args), *unused = 0;
    void *count_args[] = {&undo_count}, *save_args[] = {random};
    void *snapshot = 0;
    if (!random || !invoke(phase, random, "set_UndoCount", 1, count_args, &unused) ||
        !invoke(phase, phase->session->random_repository, "Save", 1, save_args, &unused) ||
        !read_snapshot(phase, &snapshot) || !restore_snapshot(phase, snapshot, "manual_checkpoint_restore")) {
        return 0;
    }
    struct forecast_random state;
    void *turn = 0, *map = 0, *origins = 0;
    if (!forecast_random_state(phase, &state, 0) ||
        !get(phase, phase->session->turn_repository, "FindLatest", &turn) ||
        !get_int(phase, turn, "get_Number", &phase->turn) ||
        !get_int(phase, turn, "get_CurrentPhaseBattleSide", &phase->side) || (phase->side != 0 && !entry->settled) ||
        !map_origins(phase, &map, &origins) || !read_board_size(phase, map)) {
        return 0;
    }
    state.state = entry->header.random;
    phase->moved_unit = (int) entry->header.moved_unit;
    return checkpoint_morale(phase, (struct save_unit_mp *) entry->bytes, 1) &&
        forecast_random_state(phase, &state, 1);
}

static enum command_result restore_action(struct manual_phase *phase, int forward, int settled) {
    struct battle_history *history = phase->history;
    struct battle_checkpoint **source = forward ? &history->redo : &history->last;
    unsigned int *source_count = forward ? &history->redo_count : &history->count;
    struct battle_checkpoint **destination = forward ? &history->last : &history->redo;
    unsigned int *destination_count = forward ? &history->count : &history->redo_count;
    if (!*source) {
        return COMMAND_ILLEGAL;
    }
    struct battle_checkpoint *current = write_snapshot(phase) ? capture_checkpoint(phase, phase->moved_unit, 0) : 0;
    if (!current) {
        printf("REUSE manual_history_capture_failed\n");
        return COMMAND_ILLEGAL;
    }
    /* The move marker describes the action between the two checkpoints. */
    current->header.is_move = (*source)->header.is_move;
    current->settled = settled;
    if (!restore_checkpoint(phase, *source)) {
        free(current);
        return COMMAND_FAILED;
    }
    free(pop_checkpoint(source, source_count));
    current->previous = *destination;
    *destination = current;
    ++*destination_count;
    printf("REUSE manual_%s turn %d depth %u\n", forward ? "redo" : "undo", phase->turn,
           forward ? history->redo_count : history->count);
    return COMMAND_OK;
}

static void emit_save_bytes(const void *data, unsigned long size) {
    static const char alphabet[] = "0123456789abcdef";
    const unsigned char *source = data;
    while (size) {
        char line[4097];
        unsigned long count = size < 2048 ? size : 2048;
        for (unsigned long i = 0; i < count; ++i) {
            line[2 * i] = alphabet[source[i] >> 4];
            line[2 * i + 1] = alphabet[source[i] & 15];
        }
        line[2 * count] = 0;
        printf("UI\tSAVE_CHUNK\t%s\n", line);
        source += count;
        size -= count;
    }
}

static void export_timeline(struct manual_phase *phase) {
    const struct attack_review *review = &phase->review;
    struct battle_checkpoint *current = review->snapshot
        ? capture_checkpoint(phase, review->moved_unit, review)
        : write_snapshot(phase) ? capture_checkpoint(phase, phase->moved_unit, 0) : 0;
    if (!current) {
        printf("UI\tSAVE_FAILED\n");
        return;
    }
    const struct timeline_header header = {
        .magic = BATTLE_TIMELINE_MAGIC, .stage_id = phase->session->stage->id,
        .invasion_id = phase->session->stage->invasion_id,
        .count = phase->history->count + 1,
    };
    printf("UI\tSAVE_BEGIN\n");
    emit_save_bytes(&header, sizeof(header));
    current->previous = phase->history->last;
    for (const struct battle_checkpoint *entry = current; entry; entry = entry->previous) {
        emit_save_bytes(&entry->header, sizeof(entry->header));
        emit_save_bytes(entry->bytes, checkpoint_size(&entry->header));
    }
    free(current);
    printf("UI\tSAVE_END\n");
}

static int import_timeline(struct manual_phase *phase) {
    struct timeline_header header;
    void *file = fopen(battle_path(BATTLE_TIMELINE), "rb");
    if (!file || fread(&header, sizeof(header), 1, file) != 1 || memcmp(header.magic, BATTLE_TIMELINE_MAGIC, 8) ||
        header.stage_id != phase->session->stage->id || header.invasion_id != phase->session->stage->invasion_id ||
        !header.count || header.reserved) {
        if (file) {
            fclose(file);
        }
        return 0;
    }
    struct battle_history loaded = {0};
    struct battle_checkpoint **next = &loaded.last;
    for (unsigned int i = 0; i < header.count; ++i) {
        struct checkpoint_header record;
        if (fread(&record, sizeof(record), 1, file) != 1 || !record.size || record.size > CHECKPOINT_MAX_SIZE ||
            record.moved_unit > (unsigned int) phase->session->unit_count || record.is_move > 1 ||
            record.reserved || record.unit_count != (unsigned int) phase->session->unit_count) {
            break;
        }
        unsigned long size = checkpoint_size(&record);
        struct battle_checkpoint *entry = malloc(sizeof(*entry) + size);
        if (!entry || fread(entry->bytes, 1, size, file) != size) {
            free(entry);
            break;
        }
        entry->header = record;
        entry->previous = 0;
        entry->settled = 0;
        *next = entry;
        next = &entry->previous;
        ++loaded.count;
    }
    unsigned char extra;
    int ok = loaded.count == header.count && !fread(&extra, 1, 1, file) &&
        restore_checkpoint(phase, loaded.last);
    fclose(file);
    if (ok) {
        discard_checkpoint(&loaded);
        phase->history->last = loaded.last;
        phase->history->count = loaded.count;
        printf("UI\tLOADED\t%d\t%u\n", phase->turn, loaded.count);
    } else {
        clear_battle_history(&loaded);
    }
    return ok;
}

static int read_command(char *line, unsigned long capacity) {
    unsigned long length = 0;
    char byte;
    while (read(0, &byte, 1) == 1) {
        if (byte == '\n' || byte == '\r') {
            if (length) {
                line[length] = 0;
                return 1;
            }
        } else if (length + 1 == capacity) {
            return 0;
        } else {
            line[length++] = byte;
        }
    }
    return 0;
}

static struct manual_phase manual_defense;
static int auto_defend_turn;
static unsigned int defense_action_list_handle;
static void *(*get_defense_action_list)(unsigned int);

static void emit_other_phase_state(int focus) {
    struct manual_phase *phase = &manual_defense;
    void *turn = 0, *map = 0, *origins = 0;
    if (!phase->session) {
        return;
    }
    if (!get(phase, phase->session->turn_repository, "FindLatest", &turn) ||
        !get_int(phase, turn, "get_Number", &phase->turn) || !map_origins(phase, &map, &origins) ||
        !read_board_size(phase, map) || !emit_state(phase, origins, 1, focus)) {
        printf("REUSE manual_other_phase_board_failed\n");
    }
    fflush(0);
}

void report_consumed_support(void *unit) {
    const struct manual_phase *phase = &manual_defense;
    void *id = 0;
    int number, attack, defense;
    if (phase->session && get(phase, unit, "get_Id", &id) && get_int(phase, id, "ToInt", &number) &&
        support_remaining(phase, unit, &attack, &defense)) {
        printf("UI\tSUPPORT_REMAINING\t%d\t%d\t%d\n", number, attack, defense);
        fflush(0);
    }
}

void emit_other_phase_board(void) {
    emit_other_phase_state(0);
}

void report_enemy_camera(void *unit) {
    const struct manual_phase *phase = &manual_defense;
    void *id = 0;
    int side, number;
    if (phase->session && unit && get_int(phase, unit, "get_BattleSideType", &side) &&
        (side == 2 || side == 3 || side == 5 || side == 6) &&
        get(phase, unit, "get_Id", &id) && get_int(phase, id, "ToInt", &number)) {
        emit_other_phase_state(number);
    }
}

static void *create_support_action_list(const struct manual_phase *phase, const struct battle_images *images,
                                       void *map_creator) {
    const struct battle_session *session = phase->session;
    void *args[] = {session->unit_repository, session->map_repository, session->turn_repository,
                    phase->availability, session->trait_service, map_creator, 0, 0};
    return construct(get_class(images->application, "Eternal.Application", "BattlePredictionActionSelectListDtoCreator"),
                     8, args);
}

int set_manual_defense_context(void *library, const struct battle_images *images, const struct battle_session *session,
                              const struct battle_exchange *exchange, const struct exchange_api *api,
                              void *prediction, void *availability, void *factory) {
    static void (*free_handle)(unsigned int);
    if (defense_action_list_handle) {
        free_handle(defense_action_list_handle);
        defense_action_list_handle = 0;
    }
    manual_defense = (struct manual_phase) {0};
    if (!session) {
        return 1;
    }
    struct manual_phase *phase = &manual_defense;
    phase->session = session;
    phase->exchange = exchange;
    phase->api = api;
    phase->prediction = prediction;
    phase->availability = availability;
    phase->battle_side_extensions = get_class(images->domain, "Eternal.Domain", "BattleSideTypeExtensions");
    phase->counter_class = get_class(images->domain, "Eternal.Domain", "UnitCounterSelection");
    phase->unit_id_class = get_class(images->domain, "Eternal.Domain", "IngameUnitId");
    phase->coord_class = get_class(images->domain, "Eternal.Domain", "Coord");
    phase->boxed_field = dlsym(library, "il2cpp_field_get_value_object");
    phase->set_field = dlsym(library, "il2cpp_field_set_value");
    phase->array_length = dlsym(library, "il2cpp_array_length");
    phase->array_header = dlsym(library, "il2cpp_array_object_header_size");
    unsigned int (*new_handle)(void *, unsigned char) = dlsym(library, "il2cpp_gchandle_new");
    get_defense_action_list = dlsym(library, "il2cpp_gchandle_get_target");
    free_handle = dlsym(library, "il2cpp_gchandle_free");
    void *map_creator = 0, *action_list = 0;
    if (!phase->counter_class || !phase->unit_id_class || !phase->coord_class || !phase->battle_side_extensions ||
        !phase->boxed_field || !phase->set_field ||
        !phase->array_length || !phase->array_header ||
        !new_handle || !get_defense_action_list || !free_handle ||
        !field(phase, factory, "_mapWeaponServiceCreator", &map_creator) || !map_creator ||
        !(action_list = create_support_action_list(phase, images, map_creator))) {
        return 0;
    }
    /* The enemy callback runs after this function's stack frame has gone away. */
    defense_action_list_handle = new_handle(action_list, 0);
    return defense_action_list_handle != 0;
}

/* The enemy preparation screen uses this original DTO for each selected support unit. */
static int counter_support_weapons(const struct manual_phase *phase, void *id, int number,
                                   long long requested, int *available, unsigned char *preemptive) {
    void *unit = 0, *dto = 0, *actions = 0;
    void *creator = get_defense_action_list(defense_action_list_handle);
    void *find[] = {id};
    if (!creator || !invoke(phase, phase->session->unit_repository, "FindById", 1, find, &unit) || !unit) {
        return 0;
    }
    int count;
    void *create[] = {unit};
    if (!invoke(phase, creator, "_CreateCounterSupportActionListDto", 1, create, &dto) ||
        !field(phase, dto, "ActionList", &actions) || !list_count(phase, actions, &count)) {
        return 0;
    }
    *available = 0;
    for (int i = 0; i < count; ++i) {
        void *action = 0, *list = 0, *cells = 0;
        int cell_count;
        if (!list_item(phase, actions, i, &action) || !action) {
            return 0;
        }
        if (strcmp(phase->api->class_name(phase->api->object_class(action)), "BattlePredictionWeaponDto")) {
            continue;
        }
        if (!field(phase, action, "WeaponCellDtoList", &list) ||
            !field(phase, list, "IngameUnitWeaponSelectCells", &cells) || !list_count(phase, cells, &cell_count)) {
            return 0;
        }
        for (int j = 0; j < cell_count; ++j) {
            void *cell = 0, *weapon = 0, *reasons = 0;
            long long key;
            int reason_count, map_type;
            if (!list_item(phase, cells, j, &cell) || !field(phase, cell, "AttackWeapon", &weapon) ||
                !get_int(phase, weapon, "get_MapWeaponRangeType", &map_type) ||
                !get_long(phase, weapon, "GetId", &key) ||
                !field(phase, cell, "UnavailableReasons", &reasons) ||
                !list_count(phase, reasons, &reason_count)) {
                return 0;
            }
            if (map_type || reason_count) {
                continue;
            }
            if (requested < 0) {
                void *name = 0;
                char label[256];
                int en = -1;
                get(phase, weapon, "GetName", &name);
                guarded_field(phase, cell, "EnConsumption", &en);
                format_managed_text(name, label, sizeof(label));
                printf("UI\tCOUNTER_SUPPORT_WEAPON\t%d\t%lld\t%d\t%s\n", number, key, en, label);
            } else if (key == requested) {
                int value;
                if (!get_bool(phase, weapon, "IsPreemptiveAttack", &value)) {
                    return 0;
                }
                *preemptive = value;
                *available = 1;
                return 1;
            }
        }
    }
    return 1;
}

static int emit_counter_support(const struct manual_phase *phase, void *choices) {
    void *candidates = 0;
    int count;
    if (!get(phase, choices, "Candidates", &candidates) || !list_count(phase, candidates, &count)) {
        return 0;
    }
    for (int i = 0; i < count; ++i) {
        void *candidate = 0, *id = 0;
        int number, enabled, damage;
        long long weapon;
        if (!list_item(phase, candidates, i, &candidate) || !field(phase, candidate, "UnitId", &id) ||
            !get_int(phase, id, "ToInt", &number) || !get_bool(phase, candidate, "get_IsEnabled", &enabled) ||
            !get_int(phase, candidate, "get_HitHpTotalDamage", &damage) ||
            !get_long(phase, candidate, "get_WeaponId", &weapon)) {
            return 0;
        }
        printf("UI\tCOUNTER_SUPPORT\t%d\t%d\t%lld\t%d\n", number, enabled, weapon, damage);
        int available;
        unsigned char preemptive;
        if (!counter_support_weapons(phase, id, number, -1, &available, &preemptive)) {
            printf("REUSE manual_counter_support_weapons_unavailable %d\n", number);
        }
    }
    return 1;
}

/* Zero uses the weapon already on the original candidate. */
static int apply_counter_support(const struct manual_phase *phase, void *step, void *choices, int defender,
                                 const int numbers[3], const long long weapons[3], int settling) {
    void *ids[3] = {0};
    unsigned char preemptive[3] = {0};
    for (int i = 0; i < 3; ++i) {
        int number = numbers[i];
        if (!number && !weapons[i]) {
            continue;
        }
        if (number <= 0 || number == defender || weapons[i] < 0 ||
            (i > 0 && number == numbers[0]) || (i == 2 && number == numbers[1])) {
            return 0;
        }
        void *ctor[] = {&number};
        ids[i] = construct(phase->unit_id_class, 1, ctor);
        void *find[] = {ids[i]}, *candidate = 0;
        if (!ids[i] || !invoke(phase, choices, "FindById", 1, find, &candidate) || !candidate) {
            return 0;
        }
        if (weapons[i]) {
            int available;
            if (!counter_support_weapons(phase, ids[i], number, weapons[i], &available, &preemptive[i]) || !available) {
                return 0;
            }
        }
    }

    void *unused = 0;
    unsigned char disabled = 0;
    void *disable[] = {&disabled};
    int applied = invoke(phase, choices, "SetEnabledAll", 1, disable, &unused);
    for (int i = 0; applied && i < 3; ++i) {
        if (!ids[i]) {
            continue;
        }
        void *find[] = {ids[i]}, *candidate = 0;
        applied = invoke(phase, step, "SetEnabledCounterSupport", 1, find, &unused) &&
            invoke(phase, choices, "FindEnabled", 1, find, &candidate) && candidate;
        if (applied && weapons[i]) {
            long long key = weapons[i];
            void *change[] = {&key, &preemptive[i]};
            applied = invoke(phase, candidate, "ChangeWeapon", 2, change, &unused);
        }
    }
    if (!applied) {
        return 0;
    }
    for (int i = 0; i < 3; ++i) {
        if (settling && weapons[i]) {
            printf("REUSE manual_counter_support_weapon %d %lld\n", numbers[i], weapons[i]);
        }
    }
    return 1;
}

static const char *const response_support_getters[] = {
    "get_SupportAttackUnits", "get_SupportDefendUnits",
    "get_CounterSupportUnits", "get_AttackSideSupportDefendUnits",
};
enum { RESPONSE_SUPPORT_LISTS = 4 };

static int save_response_support(const struct manual_phase *phase, void *step, void *saved[RESPONSE_SUPPORT_LISTS]) {
    for (int i = 0; i < RESPONSE_SUPPORT_LISTS; ++i) {
        void *original = 0;
        if (!get(phase, step, response_support_getters[i], &original) || !get(phase, original, "Clone", &saved[i])) {
            return 0;
        }
    }
    return 1;
}

static int restore_response_support(const struct manual_phase *phase, void *step,
                                    void *const saved[RESPONSE_SUPPORT_LISTS], void *counter) {
    static const char *const fields[] = {
        "<SupportAttackUnits>k__BackingField", "<SupportDefendUnits>k__BackingField",
        "<CounterSupportUnits>k__BackingField", "<AttackSideSupportDefendUnits>k__BackingField",
    };
    void *klass = phase->api->object_class(step), *members[RESPONSE_SUPPORT_LISTS];
    void *unused = 0;
    for (int i = 0; i < RESPONSE_SUPPORT_LISTS; ++i) {
        members[i] = phase->api->get_field(klass, fields[i]);
        if (!members[i]) {
            return 0;
        }
    }
    for (int i = 0; i < RESPONSE_SUPPORT_LISTS; ++i) {
        phase->set_field(step, members[i], saved[i]);
    }
    void *args[] = {phase->api->unbox(counter)};
    return invoke(phase, step, "SetCounterSelection", 1, args, &unused);
}

static int set_response_defender(const struct manual_phase *phase, void *choices, void *id) {
    void *unused = 0;
    unsigned char enable = 1;
    void *args[] = {id, &enable};
    return invoke(phase, choices, "SetDisabledAll", 0, 0, &unused) &&
        (!id || invoke(phase, choices, "SetEnabled", 2, args, &unused));
}

static int predict_response(const struct manual_phase *phase, void *step, void *attacker, void *target,
                            long long weapon, void *counter, void **preview) {
    unsigned char auto_support = 0;
    void *unused = 0, *value = phase->api->unbox(counter);
    void *selection[] = {value};
    void *args[] = {attacker, target, &weapon, value, &auto_support};
    return invoke(phase, step, "SetCounterSelection", 1, selection, &unused) &&
        invoke(phase, phase->prediction, "Execute", 5, args, preview) && *preview;
}

void report_manual_map_hit(void *target, int prediction, int count) {
    if (!tracing_manual_map || (prediction & 1)) {
        return;
    }
    void *id = 0;
    int number;
    if (get(&manual_defense, target, "get_Id", &id) && get_int(&manual_defense, id, "ToInt", &number)) {
        printf("REUSE manual_map_hit_count target %d count %d\n", number, count);
    }
}

void manual_choose_enemy_defender(void *attacker, void *target, long long weapon) {
    struct manual_phase *phase = &manual_defense;
    int attacker_side, target_side, attacker_manual, target_manual, count;
    void *step = 0, *choices = 0, *candidates = 0, *actor_id = 0, *target_id = 0;
    if (!phase->session || !phase->boxed_field ||
        !get_int(phase, attacker, "get_BattleSideType", &attacker_side) ||
        !get_int(phase, target, "get_BattleSideType", &target_side) ||
        !manual_side(phase, attacker_side, &attacker_manual) || attacker_manual ||
        !manual_side(phase, target_side, &target_manual) || !target_manual ||
        !get(phase, attacker, "get_Id", &actor_id) || !get(phase, target, "get_Id", &target_id)) {
        return;
    }
    int actor_number, target_number;
    if (!get_int(phase, actor_id, "ToInt", &actor_number) || !get_int(phase, target_id, "ToInt", &target_number)) {
        return;
    }
    if (auto_defend_turn || scripted_board_dirty) {
        emit_other_phase_state(auto_defend_turn ? actor_number : 0);
    }
    if (auto_defend_turn) {
        printf("REUSE original_auto_defense attacker %d target %d\n", actor_number, target_number);
        return;
    }
    if (!current_step(phase, &step) || !field(phase, step, "<SupportDefendUnits>k__BackingField", &choices) ||
        !choices || !invoke(phase, choices, "Candidates", 0, 0, &candidates) ||
        !list_count(phase, candidates, &count)) {
        return;
    }
    int numbers[count + 1];
    void *ids[count + 1];
    int enabled[count + 1];
    for (int i = 0; i < count; ++i) {
        void *candidate = 0;
        if (!list_item(phase, candidates, i, &candidate) || !field(phase, candidate, "UnitId", &ids[i]) ||
            !get_int(phase, ids[i], "ToInt", &numbers[i]) ||
            !guarded_bool_field(phase, candidate, "IsEnabled", &enabled[i])) {
            printf("REUSE manual_defense_candidates_failed\n");
            return;
        }
    }
    void *counter = 0, *type = 0, *counter_weapon = 0, *support = 0, *map = 0, *origins = 0;
    int counter_type = 0, weapon_count = 0, can_defend = 0, can_evade = 0;
    long long counter_weapon_id = 0;
    void *weapons = 0;
    struct unit_info from = {.unit = target, .id = target_id}, to = {.unit = attacker, .id = actor_id};
    if (!get(phase, step, "get_MainTargetCounterSelection", &counter) ||
        !call(phase->counter_class, "get_Type", 0, phase->api->unbox(counter), 0, &type) || !type ||
        !call(phase->counter_class, "get_WeaponId", 0, phase->api->unbox(counter), 0, &counter_weapon) || !counter_weapon ||
        !invoke(phase, step, "get_CounterSupportUnits", 0, 0, &support) ||
        !map_origins(phase, &map, &origins)) {
        return;
    }
    counter_type = *(int *) phase->api->unbox(type);
    counter_weapon_id = *(long long *) phase->api->unbox(counter_weapon);
    void *from_args[] = {target_id}, *to_args[] = {actor_id};
    if (!invoke(phase, origins, "FindByUnitId", 1, from_args, &from.origin) ||
        !invoke(phase, origins, "FindByUnitId", 1, to_args, &to.origin) ||
        !from.origin || !to.origin) {
        return;
    }
    void *moved = 0, *user_unit = 0, *master = 0;
    if (get(phase, step, "get_FreeMoveUnitCoord", &moved) && moved) {
        to.origin = moved;
    }
    void *attack_weapon_args[] = {&weapon}, *attack_weapon = 0;
    invoke(phase, attacker, "GetWeapon", 1, attack_weapon_args, &attack_weapon);
    if (counter_type != 4) {
        if (!get_bool(phase, target, "get_CanDefend", &can_defend) ||
            !get(phase, target, "get_UserUnit", &user_unit) || !get(phase, user_unit, "get_MasterUnit", &master) ||
            !guarded_bool_field(phase, master, "CanEvade", &can_evade) ||
            !weapon_list(phase, &from, &to, &weapons, &weapon_count)) {
            return;
        }
    }
    printf("UI\tDEFENSE\t%d\t%d\t%lld\n", actor_number, target_number, weapon);
    emit_footprint(phase, "DEFENSE_FOOTPRINT", actor_number, &to);
    emit_footprint(phase, "DEFENSE_FOOTPRINT", target_number, &from);
    emit_defense_energy(phase, actor_number, attacker);
    emit_defense_energy(phase, target_number, target);
    printf("UI\tDEFENSE_ACTIONS\t%d\t%lld\t%d\t%d\n", counter_type, counter_weapon_id, can_defend, can_evade);
    for (int i = 0; i < weapon_count; ++i) {
        void *candidate = 0;
        long long key;
        if (weapon_at(phase, weapons, i, &candidate, &key)) {
            printf("UI\tCOUNTER_WEAPON\t%lld\n", key);
        }
    }
    if (support) {
        emit_counter_support(phase, support);
    }
    for (int i = 0; i < count; ++i) {
        printf("UI\tSUPPORT_DEFENSE\t%d\t%d\n", numbers[i], enabled[i] != 0);
    }
    char command[160];
    int original = 0;
    for (int i = 0; i < count; ++i) {
        if (enabled[i]) {
            original = numbers[i];
        }
    }
    printf("UI\tDEFENSE_READY\n");
    fflush(0);
    for (;;) {
        if (!read_command(command, sizeof(command))) {
            return;
        }
        if (!strcmp(command, "d auto") || !strcmp(command, "d auto_turn")) {
            auto_defend_turn = !strcmp(command, "d auto_turn");
            printf("REUSE manual_support_defense_selected %d\n", original);
            printf("UI\tDEFENSE_ACCEPTED\n");
            fflush(0);
            return;
        }
        char mode;
        int selected, action = -1, counters[3] = {-1, 0, 0};
        long long choice_weapon = 0, support_weapons[3] = {0};
        if (sscanf(command, "%c %d %d %lld %d %d %d %lld %lld %lld", &mode, &selected, &action, &choice_weapon,
                   &counters[0], &counters[1], &counters[2],
                   &support_weapons[0], &support_weapons[1], &support_weapons[2]) < 2 || (mode != 'd' && mode != 'f')) {
            return;
        }
        int forecasting = mode == 'f';
        void *choices_for_plan = 0, *support_for_plan = 0, *id = 0;
        if (!get(phase, step, "get_SupportDefendUnits", &choices_for_plan) ||
            !get(phase, step, "get_CounterSupportUnits", &support_for_plan)) {
            return;
        }
        for (int i = 0; i < count; ++i) {
            if (numbers[i] == selected) {
                id = ids[i];
                break;
            }
        }
        int valid = action < 0 || (action == 2 && can_evade) || (action == 3 && can_defend && !id);
        if (action == 1) {
            for (int i = 0; i < weapon_count && !valid; ++i) {
                void *candidate = 0;
                long long key;
                valid = weapon_at(phase, weapons, i, &candidate, &key) && key == choice_weapon;
            }
        }

        if (!valid || (selected && !id)) {
            printf("UI\t%s\n", forecasting ? "DEFENSE_FORECAST_FAILED" : "DEFENSE_REJECTED");
            fflush(0);
            continue;
        }
        int original_response = selected == original && action < 0 && counters[0] < 0;
        if (!forecasting && original_response) {
            printf("REUSE manual_support_defense_selected %d\n", selected);
            printf("UI\tDEFENSE_ACCEPTED\n");
            fflush(0);
            return;
        }
        void *saved[RESPONSE_SUPPORT_LISTS], *preview = 0;
        void *response = counter;
        struct forecast_random random;
        if (id && action < 0 && counter_type == 3 && can_evade && selected != original) {
            action = 2;
        }
        if (action >= 0) {
            const char *method = action == 1 ? "CreateCounter" : action == 2 ? "CreateEvade" : "CreateDefend";
            void *args[] = {&choice_weapon};
            if (!call(phase->counter_class, method, action == 1, 0, args, &response) || !response) {
                printf("UI\t%s\n", forecasting ? "DEFENSE_FORECAST_FAILED" : "DEFENSE_REJECTED");
                fflush(0);
                continue;
            }
        }
        if (!save_response_support(phase, step, saved) || !forecast_random_state(phase, &random, 0)) {
            printf("UI\t%s\n", forecasting ? "DEFENSE_FORECAST_FAILED" : "DEFENSE_REJECTED");
            fflush(0);
            continue;
        }
        void *previous = 0;
        if (!get(phase, step, "get_MainTargetCounterSelection", &previous)) {
            return;
        }
        int ok = counters[0] < 0 || apply_counter_support(phase, step, support_for_plan, selected,
                                                         counters, support_weapons, !forecasting);
        ok = ok && set_response_defender(phase, choices_for_plan, id) &&
            predict_response(phase, step, attacker, target, weapon, response, &preview);
        if (forecasting && ok) {
            printf("UI\tDEFENSE_FORECAST\n");
            ok = emit_prediction_details(phase, "DEFENSE_DAMAGE", preview);
            if (!emit_received_damage(phase, "DEFENSE_RECEIVED", preview)) {
                printf("REUSE manual_defense_damage_totals_unavailable\n");
            }
            if (!emit_battle_order(phase, "DEFENSE_BATTLE_ACTION", preview, step, actor_id, target_id, attack_weapon,
                                   origins)) {
                printf("REUSE manual_defense_actions_unavailable\n");
            }
        }
        if (forecasting || !ok) {
            if (!restore_response_support(phase, step, saved, previous) || !forecast_random_state(phase, &random, 1)) {
                printf("REUSE manual_defense_restore_failed\n");
                return;
            }
            printf("UI\t%s\n", forecasting ? (ok ? "DEFENSE_FORECAST_READY" : "DEFENSE_FORECAST_FAILED")
                                           : "DEFENSE_REJECTED");
            fflush(0);
            continue;
        }
        printf("REUSE manual_support_defense_selected %d\n", selected);
        printf("REUSE manual_counter_selected %d weapon %lld\n", action, choice_weapon);
        if (counters[0] >= 0) {
            printf("REUSE manual_counter_support_selected %d %d %d\n", counters[0], counters[1], counters[2]);
        }
        printf("UI\tDEFENSE_ACCEPTED\n");
        fflush(0);
        return;
    }
}

enum manual_phase_result run_manual_phase(void *library, const struct battle_images *images,
                                          const struct battle_session *session, const struct battle_exchange *exchange,
                                          const struct combat_services *combat, const struct stage_phase_services *phases,
                                          const struct exchange_api *api, const struct player_loop *loop,
                                          void *token, int *turn, int *side, struct battle_history *history, int settled) {
    auto_defend_turn = 0;
    void *(*string_new)(const char *) = dlsym(library, "il2cpp_string_new");
    struct manual_phase phase = {
        .session = session, .exchange = exchange, .api = api, .loop = loop,
        .unit_id_class = get_class(images->domain, "Eternal.Domain", "IngameUnitId"),
        .coord_class = get_class(images->domain, "Eternal.Domain", "Coord"),
        .battle_side_extensions = get_class(images->domain, "Eternal.Domain", "BattleSideTypeExtensions"),
        .counter_class = get_class(images->domain, "Eternal.Domain", "UnitCounterSelection"),
        .participation_class = get_class(images->domain, "Eternal.Domain", "BattleParticipationStatus"),
        .calculator = combat->calculator,
        .availability = combat->availability, .move_options = phases->movement_options,
        .token = token, .turn = *turn, .side = *side, .history = history,
        .random_class = get_class(images->domain, "Eternal.Domain", "StageRandomEntity"),
        .new_object = dlsym(library, "il2cpp_object_new"), .boxed_field = dlsym(library, "il2cpp_field_get_value_object"),
        .set_field = dlsym(library, "il2cpp_field_set_value"),
        .array_length = dlsym(library, "il2cpp_array_length"),
        .array_header = dlsym(library, "il2cpp_array_object_header_size"),
        .array_new_specific = dlsym(library, "il2cpp_array_new_specific"),
        .element_class = dlsym(library, "il2cpp_class_get_element_class"),
        .value_size = dlsym(library, "il2cpp_class_value_size"),
        .value_box = dlsym(library, "il2cpp_value_box"),
    };
    void *factory = phases->use_cases;
    void *map = 0, *origins = 0;
    void *skill_args[] = {exchange->memento, session->unit_repository, session->trait_service, session->turn_repository};
    void *target_args[] = {session->unit_repository, session->map_repository};
    phase.skill_service = construct(get_class(images->domain, "Eternal.Domain", "IngameSkillService"), 4, skill_args);
    phase.unit_skill_targets = construct(get_class(images->domain, "Eternal.Domain", "UnitSkillTargetSelector"), 2,
                                        target_args);
    phase.character_skill_targets = construct(get_class(images->domain, "Eternal.Domain", "CharacterSkillTargetSelector"),
                                             2, target_args);
    if (!string_new || !phase.unit_id_class || !phase.coord_class || !phase.battle_side_extensions ||
        !phase.counter_class || !phase.participation_class || !phase.calculator || !phase.random_class ||
        !phase.new_object || !phase.boxed_field || !phase.set_field || !phase.array_length || !phase.array_header ||
        !phase.array_new_specific || !phase.element_class || !phase.value_size || !phase.value_box ||
        !phase.skill_service || !phase.unit_skill_targets || !phase.character_skill_targets ||
        !get(&phase, factory, "CreateCreateNewStepUseCase", &phase.new_step) ||
        !get(&phase, factory, "CreateMoveUseCase", &phase.move) ||
        !get(&phase, factory, "CreateResetMoveUseCase", &phase.reset_move) ||
        !get(&phase, factory, "CreatePlayerAttackPredictionUseCase", &phase.prediction) ||
        !get(&phase, factory, "CreateAttackUseCase", &phase.attack) ||
        !get(&phase, factory, "CreateStandbyUseCase", &phase.standby) ||
        !field(&phase, factory, "_availableCharacterSkillService", &phase.character_availability) ||
        !phase.character_availability ||
        !get(&phase, factory, "CreateCharacterSkillExecuteUseCase", &phase.character_skill) ||
        !get(&phase, factory, "CreateUnitSkillExecuteUseCase", &phase.unit_skill) ||
        !get(&phase, factory, "CreateSupporterActiveSkillExecuteUseCase", &phase.supporter_skill) ||
        !field(&phase, factory, "_supporterSkillTargetSelector", &phase.supporter_skill_targets) ||
        !phase.supporter_skill_targets ||
        !get(&phase, factory, "CreateMapWeaponSelectUseCase", &phase.map_select) ||
        !get(&phase, factory, "CreateMapWeaponExecuteUseCase", &phase.map_execute) ||
        !field(&phase, factory, "_mapWeaponServiceCreator", &phase.map_creator) || !phase.map_creator ||
        !bind_map_selection(library, &phase) ||
        !map_origins(&phase, &map, &origins) || !read_board_size(&phase, map)) {
        return MANUAL_ERROR;
    }
    phase.support_action_list = create_support_action_list(&phase, images, phase.map_creator);
    if (bind_board_marker_output(library, images, session->view)) {
        get(&phase, factory, "CreateDisplaySupportAndCautionUseCase", &phase.caution);
    } else {
        printf("REUSE board_marker_output_unavailable\n");
    }
    phase.support_plan = malloc(session->unit_count * sizeof(*phase.support_plan));
    struct save_unit_mp morale[session->unit_count];
    phase.review.morale = morale;
    phase.snapshot_name = string_new("ManualAction");
    if (!phase.support_action_list || !phase.support_plan || !phase.snapshot_name) {
        free(phase.support_plan);
        return MANUAL_ERROR;
    }
    enum manual_phase_result outcome = MANUAL_QUIT;
    char line[128];
    if (settled) {
        for (;;) {
            printf("UI\tSETTLED\t%u\n", history->count);
            fflush(0);
            if (!read_command(line, sizeof(line)) || !strcmp(line, "quit")) {
                break;
            }
            if (!strcmp(line, "undo")) {
                enum command_result result = restore_action(&phase, 0, 1);
                if (result != COMMAND_ILLEGAL) {
                    outcome = result == COMMAND_OK ? MANUAL_REWOUND : MANUAL_ERROR;
                    break;
                }
                printf("UI\tREJECTED\n");
            }
        }
        goto done;
    }
    if (history->load_pending || history->mumu_checkpoint_pending) {
        history->load_pending = 0;
        for (int number = 1; number <= session->unit_count; ++number) {
            struct unit_info unit;
            if (!lookup(&phase, origins, number, &unit)) {
                outcome = MANUAL_ERROR;
                goto done;
            }
            if (unit.origin && (unit.side == 2 || unit.side == 3 || unit.side == 5 || unit.side == 6)) {
                printf("UI\tOPENING_ENEMY\t%d\n", number);
            }
        }
        int loaded = history->mumu_checkpoint_pending ? import_mumu_checkpoint(&phase, phases) : import_timeline(&phase);
        history->mumu_checkpoint_pending = 0;
        if (!loaded || !map_origins(&phase, &map, &origins)) {
            printf("UI\tLOAD_FAILED\n");
            outcome = MANUAL_ERROR;
            goto done;
        }
    }
    if (!emit_state(&phase, origins, 0, 0)) {
        outcome = MANUAL_ERROR;
        goto done;
    }
    for (;;) {
        printf("UI\tREADY\n");
        fflush(0);
        if (!read_command(line, sizeof(line)) || !strcmp(line, "quit")) {
            break;
        }
        printf("REUSE manual_command %s\n", line);
        fflush(0);
        if (!strcmp(line, "review back")) {
            phase.review.closed = 1;
            continue;
        }
        if (!strcmp(line, "save")) {
            export_timeline(&phase);
            continue;
        }
        if ((phase.review.snapshot && !review_matches_command(&phase.review, line) && !discard_attack_review(&phase)) ||
            !map_origins(&phase, &map, &origins)) {
            outcome = MANUAL_ERROR;
            break;
        }
        if (!strcmp(line, "pass")) {
            outcome = save_action(&phase) && commit_movement(&phase, origins) ? MANUAL_PASS : MANUAL_ERROR;
            if (outcome == MANUAL_PASS) {
                clear_redo(history);
            }
            break;
        }
        if (!strcmp(line, "v cancel")) {
            phase.skill_actor = 0;
            phase.selected_skill_candidates = phase.selected_skill_targets = 0;
            continue;
        }
        int actor = 0, target = 0, weapon = 0, skill_index = -1;
        int supporters[3] = {-1, 0, 0};
        int defender = -1;
        int support_unit = 0;
        long long support_weapon = -1;
        long long map_weapon = 0;
        char map_mode = 0;
        struct coord destination = {0};
        int forward = !strcmp(line, "redo"), backward = !strcmp(line, "undo");
        int redo_settlement = forward && history->redo && history->redo->settled;
        enum command_result result = COMMAND_ILLEGAL;
        if (!strcmp(line, "state")) {
            if (emit_state(&phase, origins, 0, 0)) {
                continue;
            }
            result = COMMAND_FAILED;
        } else if (sscanf(line, "inspect %d", &actor) == 1) {
            if (emit_options(&phase, map, origins, actor) && emit_skills(&phase, origins, actor) &&
                emit_map_weapons(&phase, origins, actor)) {
                continue;
            }
            result = COMMAND_FAILED;
        } else if (sscanf(line, "weapons %d", &actor) == 1) {
            struct unit_info unit;
            if (lookup(&phase, origins, actor, &unit) && unit.unit && unit.origin) {
                emit_weapon_catalog(&phase, &unit, actor, 1, 0);
            }
            continue;
        } else if (sscanf(line, "m %d %d %d", &actor, &destination.x, &destination.z) == 3) {
            result = move_unit(&phase, origins, actor, destination);
        } else if (sscanf(line, "back %d", &actor) == 1) {
            result = cancel_move(&phase, origins, actor);
            if (result == COMMAND_OK) {
                clear_redo(history);
                phase.support_actor = phase.map_actor = phase.skill_actor = 0;
                phase.selected_skill_candidates = phase.selected_skill_targets = 0;
                if (!map_origins(&phase, &map, &origins) || !emit_state(&phase, origins, 0, 0)) {
                    outcome = MANUAL_ERROR;
                    break;
                }
                continue;
            }
        } else if (sscanf(line, "s %d", &actor) == 1) {
            result = standby_unit(&phase, origins, actor);
        } else if (sscanf(line, "estimates %d %d %d", &actor, &target, &weapon) == 3) {
            result = estimate_target(&phase, origins, actor, target, weapon);
            if (result == COMMAND_OK) {
                continue;
            }
        } else if (sscanf(line, "e %d %d %d", &actor, &target, &weapon) == 3) {
            result = attack_unit(&phase, origins, actor, target, weapon, supporters, defender, WEAPON_ESTIMATE, 0, 0);
            if (result == COMMAND_OK) {
                continue;
            }
        } else if (sscanf(line, "p %d %d %d %d %d %d %d", &actor, &target, &weapon,
                          &supporters[0], &supporters[1], &supporters[2], &defender) >= 3) {
            result = attack_unit(&phase, origins, actor, target, weapon, supporters, defender, BATTLE_PREVIEW, 0, 0);
            if (result == COMMAND_OK) {
                continue;
            }
        } else if (sscanf(line, "b %d %d %d %d %lld %d", &actor, &target, &weapon,
                          &support_unit, &support_weapon, &defender) >= 5 && support_unit > 0) {
            supporters[0] = -2;
            result = attack_unit(&phase, origins, actor, target, weapon, supporters, defender, BATTLE_PREVIEW,
                                 support_unit, support_weapon);
            if (result == COMMAND_OK) {
                continue;
            }
        } else if (sscanf(line, "a %d %d %d %d %d %d %d", &actor, &target, &weapon,
                          &supporters[0], &supporters[1], &supporters[2], &defender) >= 3) {
            result = attack_unit(&phase, origins, actor, target, weapon, supporters, defender, EXECUTE_ATTACK, 0, 0);
        } else if (sscanf(line, "c %d %d", &actor, &target) == 2 && target >= 0) {
            result = use_skill(&phase, origins, actor, target);
        } else if (sscanf(line, "u %d", &actor) == 1) {
            result = use_skill(&phase, origins, actor, -1);
        } else if (sscanf(line, "h %d", &actor) == 1) {
            result = use_supporter_skill(&phase, map, origins, actor);
        } else if (sscanf(line, "v %d %d", &actor, &skill_index) == 2 && skill_index == SUPPORTER_SKILL) {
            int fields = sscanf(line, "v %d %d %d %d %d", &actor, &skill_index, &target,
                                &destination.x, &destination.z);
            if (fields == 2 || (fields == 5 && target > 0)) {
                result = select_supporter_skill(&phase, map, origins, actor, target, destination);
            }
            if (result == COMMAND_OK) {
                continue;
            }
        } else if (sscanf(line, "v %d %d %d", &actor, &skill_index, &target) >= 2 && skill_index >= -1) {
            result = select_skill(&phase, map, origins, actor, skill_index, target);
            if (result == COMMAND_OK) {
                continue;
            }
        } else if (sscanf(line, "w %d %lld %c %d %d", &actor, &map_weapon, &map_mode, &target, &weapon) >= 3) {
            result = map_weapon_action(&phase, origins, actor, map_weapon, map_mode, target, weapon);
            if (result == COMMAND_OK && map_mode != 'f') {
                continue;
            }
        } else if (forward || backward) {
            result = restore_action(&phase, forward, 0);
        }
        if (result == COMMAND_ILLEGAL) {
            printf("UI\tREJECTED\n");
            continue;
        }
        phase.map_actor = 0;
        phase.support_actor = 0;
        phase.skill_actor = 0;
        phase.selected_skill_candidates = phase.selected_skill_targets = 0;
        if (result == COMMAND_OK && redo_settlement) {
            outcome = map_origins(&phase, &map, &origins) && emit_state(&phase, origins, 1, 0)
                ? MANUAL_RESTORED_SETTLEMENT : MANUAL_ERROR;
            break;
        }
        void *context = 0;
        void *args[] = {token};
        int finished = 0;
        if (result == COMMAND_FAILED ||
            (!forward && !backward &&
             !task(&phase, "manual_conditions", exchange->conditions, "CheckAndPlayEventAsync", 1, args, 0)) ||
            !get(&phase, session->context_repository, "Get", &context) ||
            !get_bool(&phase, context, "IsBattleFinished", &finished)) {
            outcome = MANUAL_ERROR;
            break;
        }
        if (!forward && !backward) {
            clear_redo(history);
        }
        if (finished) {
            outcome = MANUAL_FINISHED;
            break;
        }
        if (!map_origins(&phase, &map, &origins) || !emit_state(&phase, origins, 0, 0)) {
            outcome = MANUAL_ERROR;
            break;
        }
    }
done:
    *turn = phase.turn;
    *side = phase.side;
    free(phase.support_plan);
    return outcome;
}
