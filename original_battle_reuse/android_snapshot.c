/* Read a copied 2.8.0 checkpoint with the original save decoder. */
#include "android_runtime.h"

extern void *dlsym(void *, const char *);
extern int printf(const char *, ...);
extern int strncmp(const char *, const char *, unsigned long);
extern int bind_unity_services(void *);

/* The host verifies the exact libil2cpp and metadata hashes before launching. */
#define MEMORY_PACK_SPAN_CONSTRUCTOR 0xafc6504

enum { OPENING_MEMBER_COUNT = 4, SAVE_MEMBER_COUNT = 18, SAVE_VERSION_BYTES = 4, MEMORY_PACK_NULL = 255 };

struct snapshot_api {
    void *(*object_class)(void *);
    const char *(*class_name)(void *);
    void *(*get_field)(void *, const char *);
    void *(*box_field)(void *, void *);
    void *(*unbox)(void *);
    int (*value_size)(void *, unsigned int *);
    void *(*string_new)(const char *);
};

static struct snapshot_api api;

static void *application_version(void) {
    return api.string_new("2.8.0");
}

static void *field(void *object, const char *name) {
    void *member = object ? api.get_field(api.object_class(object), name) : 0;
    return member ? api.box_field(member, object) : 0;
}

static int invoke(void *object, const char *name, int arity, void **args, void **result) {
    return object && call(api.object_class(object), name, arity, object, args, result) && *result;
}

static void *unwrap(void *object) {
    void *raw = 0;
    if (object && !strncmp(api.class_name(api.object_class(object)), "Obscured", 8)) {
        return call(api.object_class(object), "ToRaw", 0, api.unbox(object), 0, &raw) ? raw : 0;
    }
    return object;
}

static int scalar(void *value, int size, long long *result) {
    unsigned int alignment = 0;
    value = unwrap(value);
    if (!value || api.value_size(api.object_class(value), &alignment) != size) {
        return 0;
    }
    void *raw = api.unbox(value);
    *result = size == 8 ? *(long long *) raw : size == 4 ? *(int *) raw :
              size == 2 ? *(short *) raw : *(unsigned char *) raw;
    return 1;
}

static int number(const char *name, void *value, int size) {
    long long n;
    if (!scalar(value, size, &n)) {
        printf("SNAPSHOT missing scalar %s\n", name);
        return 0;
    }
    printf("FIELD\t%s\t%lld\n", name, n);
    return 1;
}

static void *list_item(void *list, int index) {
    void *row = 0, *args[] = {&index};
    return invoke(list, "get_Item", 1, args, &row) ? row : 0;
}

static int list_count(void *list, int *count) {
    void *result = 0;
    *count = 0;
    if (!list) {
        return 1;
    }
    if (!invoke(list, "get_Count", 0, 0, &result)) {
        return 0;
    }
    *count = *(int *) api.unbox(result);
    return *count >= 0 && *count <= 4096;
}

static int emit_unit(void *row) {
    void *saved_coord = field(row, "OriginCoord");
    void *coord = unwrap(saved_coord);
    if (saved_coord && !coord) {
        return 0;
    }
    void *group = field(row, "IngameUnitGroup");
    if (!number("id", field(field(row, "Id"), "_ingameUnitId"), 4) ||
        !number("master_unit_id", field(field(row, "MasterUnitId"), "_id"), 8) ||
        !number("npc_id", field(field(row, "NpcId"), "_id"), 8) ||
        !number("direction", field(row, "Direction"), 4) ||
        !number("existence_type", field(row, "ExistenceType"), 4) ||
        !number("step_order", field(field(row, "UnitStepOrder"), "_value"), 4) ||
        !number("side", field(group, "BattleSideType"), 4) ||
        !number("formation_id", field(field(group, "FormationId"), "Id"), 2) ||
        !number("formation_link_type", field(group, "FormationLinkType"), 4) ||
        !number("leader", field(row, "IsLeader"), 1)) {
        return 0;
    }
    if (coord && (!number("x", field(coord, "X"), 4) || !number("z", field(coord, "Z"), 4))) {
        return 0;
    }
    void *unit = field(row, "UserUnitEntity");
    void *character = field(row, "UserCharacterEntity");
    void *weapons = field(unit, "MainWeaponLevelList");
    void *parts = field(unit, "OptionPartsIds");
    void *cores = field(unit, "UnitCustomCoreEntityList");
    int weapon_count, part_count, core_count;
    if ((unit && (!number("unit_level", field(unit, "Level"), 2) ||
                  !number("unit_limit_break", field(unit, "LimitBreakStep"), 2) ||
                  !number("unit_ssp_level", field(unit, "SspLevel"), 2) ||
                  !number("unit_specialized", field(unit, "IsSpecialize"), 1))) ||
        (character && (!number("character_id", field(character, "CharacterId"), 8) ||
                       !number("character_level", field(character, "Level"), 2) ||
                       !number("pilot_specialized", field(character, "IsSpecialized"), 1))) ||
        !list_count(weapons, &weapon_count) ||
        !list_count(parts, &part_count) || !list_count(cores, &core_count)) {
        return 0;
    }
    printf("FIELD\toption_part_count\t%d\n", part_count);
    for (int i = 0; i < part_count; ++i) {
        void *part = 0, *args[] = {&i};
        printf("PART\n");
        if (!invoke(parts, "get_Item", 1, args, &part) ||
            !number("option_part_id", part, 8)) {
            return 0;
        }
    }
    for (int i = 0; i < core_count; ++i) {
        void *core = 0, *args[] = {&i};
        printf("CORE\n");
        if (!invoke(cores, "get_Item", 1, args, &core) ||
            !number("core_id", field(core, "UnitSspCustomCoreId"), 8) ||
            !number("core_point", field(core, "Point"), 4) ||
            !number("core_release_point", field(core, "ReleasePoint"), 4)) {
            return 0;
        }
    }
    for (int i = 0; i < weapon_count; ++i) {
        void *weapon = 0;
        void *args[] = {&i};
        printf("WEAPON\n");
        if (!invoke(weapons, "get_Item", 1, args, &weapon) ||
            !number("main_weapon_id", field(weapon, "MainWeaponId"), 8) ||
            !number("weapon_level", field(weapon, "WeaponLevel"), 4)) {
            return 0;
        }
    }
    return 1;
}

static void *decrypt_checkpoint(void *library, const struct battle_images *images) {
    void *save_class = get_class(images->infrastructure, "Eternal.Infrastructure", "EternalLocalSaveDataService");
    void *save_args[] = {0};
    void *service = construct(save_class, 1, save_args);
    void *iv = field(service, "_iv");
    void *aes_class = get_class(images->application, "Eternal.Application", "WhiteAes");
    void (*class_init)(void *) = dlsym(library, "il2cpp_runtime_class_init");
    void (*read_static)(void *, void *) = dlsym(library, "il2cpp_field_static_get_value");
    long length = 0;
    void *bytes = read_managed_bytes(library, battle_path("checkpoint.bin"), &length);
    if (!iv || !aes_class || !class_init || !read_static || !bytes || length <= 0) {
        return 0;
    }
    class_init(aes_class);
    void *member = api.get_field(aes_class, "DEFAULT"), *aes = 0, *plain = 0;
    if (!member) {
        return 0;
    }
    read_static(member, &aes);
    void *args[] = {bytes, iv};
    return invoke(aes, "Decrypt", 2, args, &plain) ? plain : 0;
}

static int bind_progress_reader(const struct battle_images *images, const struct library_address *runtime) {
    void *klass = get_class(images->infrastructure, "Eternal.Infrastructure", "IngameMemoryPackFormatterRegister");
    void *dependencies[] = {0, 0, 0, 0, 0, 0};
    if (!construct(klass, 6, dependencies)) {
        return 0;
    }
    /* Each formatter consumes its serialized DTO before this point. Only the
       metadata reader skips reconstructing objects that need live repositories. */
    static const struct {
        unsigned long entry, finish;
        unsigned int original;
    } formatters[] = {
        {0xaa13c9c, 0xaa13cf0, 0xf9400a96}, /* battle context */
        {0xaa14a3c, 0xaa14a9c, 0xb4000660}, /* trait */
        {0xaa15060, 0xaa151d8, 0xb4000d20}, /* trait set */
        {0xaa15394, 0xaa15440, 0xf9400a95}, /* map */
        {0xaa155ec, 0xaa15640, 0xf9400a95}, /* map root */
        {0xaa15854, 0xaa158ac, 0xb4000520}, /* unit master */
        {0xaa15a88, 0xaa15ae0, 0xb4000520}, /* weapon master */
    };
    for (unsigned int i = 0; i < sizeof(formatters) / sizeof(*formatters); ++i) {
        unsigned int *entry = (void *) ((char *) runtime->base + formatters[i].entry);
        unsigned int branch = 0x14000000U | ((formatters[i].finish - formatters[i].entry) >> 2);
        if (*entry != formatters[i].original || !replace_instructions(entry, &branch, 1)) {
            return 0;
        }
    }
    return 1;
}

static void *decode_save(void *library, const struct battle_images *images, const void *memory_pack,
                         int read_progress, int *has_latest_step) {
    void *plain = decrypt_checkpoint(library, images);
    void *state_class = get_class(memory_pack, "MemoryPack", "MemoryPackReaderOptionalState");
    void *state = construct(state_class, 0, 0), *unused = 0;
    void *options[] = {0};
    unsigned int (*array_header)(void) = dlsym(library, "il2cpp_array_object_header_size");
    unsigned long (*array_length)(void *) = dlsym(library, "il2cpp_array_length");
    void *runtime = dlsym(library, "il2cpp_runtime_invoke");
    struct library_address info;
    if (!plain || !state || !array_header || !array_length || !runtime || !dladdr(runtime, &info) ||
        !call(state_class, "Init", 1, state, options, &unused)) {
        return 0;
    }
    struct span { void *bytes; unsigned long length; };
    void (*reader_init)(void *, struct span, void *, void *) =
        (void *) ((char *) info.base + MEMORY_PACK_SPAN_CONSTRUCTOR);
    unsigned long length = array_length(plain);
    if (length <= SAVE_VERSION_BYTES) {
        return 0;
    }
    unsigned char *bytes = (unsigned char *) plain + array_header();
    unsigned char *header = bytes + SAVE_VERSION_BYTES;
    if (*header != SAVE_MEMBER_COUNT) {
        return 0;
    }
    unsigned long reader[16] = {0};
    /* Limit the decrypted copy to the fields needed by the selected export. */
    if (read_progress && !bind_progress_reader(images, &info)) {
        return 0;
    }
    *header = OPENING_MEMBER_COUNT + read_progress;
    reader_init(reader, (struct span) {bytes, length}, state, 0);
    void *data = 0, *args[] = {reader, &data};
    void *save = get_class(images->application, "Eternal.Application", "IngameAllMemoryPackSaveData");
    int decoded = call(save, "Deserialize", 2, 0, args, &unused);
    *header = SAVE_MEMBER_COUNT;
    if (!decoded || !data) {
        return 0;
    }
    if (read_progress) {
        *has_latest_step = field(data, "LatestStep") != 0;
        return data;
    }
    /* The next serialized field is LatestStep. Its null marker can be read without loading it. */
    void *reader_class = get_class(memory_pack, "MemoryPack", "MemoryPackReader");
    void *consumed = 0;
    if (!call(reader_class, "get_Consumed", 0, reader, 0, &consumed) || !consumed) {
        return 0;
    }
    int offset = *(int *) api.unbox(consumed);
    if (offset <= SAVE_VERSION_BYTES || (unsigned long) offset >= length) {
        return 0;
    }
    *has_latest_step = bytes[offset] != MEMORY_PACK_NULL;
    return data;
}

static int emit_progress(void *latest) {
    int count = 0;
    void *turns = field(field(latest, "TurnList"), "_values");
    if (!list_count(turns, &count) || !count) {
        return 0;
    }
    void *turn = list_item(turns, count - 1), *number = 0, *side = 0;
    long long turn_number, battle_side;
    if (!invoke(turn, "get_Number", 0, 0, &number) || !scalar(number, 4, &turn_number) ||
        !invoke(turn, "get_CurrentPhaseBattleSide", 0, 0, &side) || !scalar(side, 4, &battle_side)) {
        return 0;
    }
    printf("PROGRESS\t%lld\t%lld\n", turn_number, battle_side);
    void *supporters = field(field(latest, "SupporterList"), "Values");
    if (!list_count(supporters, &count)) {
        return 0;
    }
    for (int i = 0; i < count; ++i) {
        void *row = list_item(supporters, i);
        void *group = field(row, "IngameUnitGroup");
        long long group_side, force, master, id, exp, stars;
        if (!scalar(field(group, "BattleSideType"), 4, &group_side)) {
            return 0;
        }
        if (group_side != 0) {
            continue;
        }
        if (!scalar(field(field(group, "FormationId"), "Id"), 2, &force) ||
            !scalar(field(row, "MasterSupporterId"), 8, &master) || !scalar(field(row, "IngameSupporterId"), 8, &id) ||
            !scalar(field(row, "Exp"), 8, &exp) || !scalar(field(row, "LimitBreakStep"), 2, &stars)) {
            return 0;
        }
        printf("SUPPORTER_PROGRESS\t%lld\t%lld\t%lld\t%lld\t%lld\n", force, master, id, exp, stars);
    }
    return 1;
}

static int export_snapshot(void *library, const struct battle_images *images, const void *memory_pack, int read_progress) {
    if (!images->application || !images->infrastructure || !memory_pack) {
        return 0;
    }
    api = (struct snapshot_api) {
        .object_class = dlsym(library, "il2cpp_object_get_class"),
        .class_name = dlsym(library, "il2cpp_class_get_name"),
        .get_field = dlsym(library, "il2cpp_class_get_field_from_name"),
        .box_field = dlsym(library, "il2cpp_field_get_value_object"),
        .unbox = dlsym(library, "il2cpp_object_unbox"),
        .value_size = dlsym(library, "il2cpp_class_value_size"),
        .string_new = dlsym(library, "il2cpp_string_new"),
    };
    void (*add_icall)(const char *, void *) = dlsym(library, "il2cpp_add_internal_call");
    if (!api.object_class || !api.class_name || !api.get_field || !api.box_field ||
        !api.unbox || !api.value_size || !api.string_new || !add_icall ||
        !bind_unity_services(library)) {
        return 0;
    }
    add_icall("UnityEngine.Application::get_version", application_version);
    int has_latest_step;
    void *data = decode_save(library, images, memory_pack, read_progress, &has_latest_step);
    void *initial = field(data, "InitialUnitSetList");
    int count;
    if (!data || !initial || !list_count(initial, &count) || !count ||
        !number("stage_id", field(field(data, "StageId"), "_id"), 8) ||
        !number("stage_total_start_count", field(data, "StageTotalStartCount"), 8)) {
        return 0;
    }
    printf("LATEST\t%d\nCOUNT\t%d\n", has_latest_step, count);
    for (int i = 0; i < count; ++i) {
        void *row = list_item(initial, i);
        printf("UNIT\n");
        if (!row || !emit_unit(row)) {
            return 0;
        }
    }
    if (read_progress && has_latest_step && !emit_progress(field(data, "LatestStep"))) {
        return 0;
    }
    printf("SNAPSHOT_COMPLETE\n");
    return 1;
}

int export_emulator_snapshot(void *library, const struct battle_images *images, const void *memory_pack) {
    return export_snapshot(library, images, memory_pack, 0);
}

int export_emulator_progress(void *library, const struct battle_images *images, const void *memory_pack) {
    return export_snapshot(library, images, memory_pack, 1);
}
