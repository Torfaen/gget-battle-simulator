/* Native guest process for the extracted Android libraries. */

#include "android_runtime.h"

typedef unsigned long size_t;

extern void *dlopen(const char *path, int flags);
extern void *dlsym(void *handle, const char *name);
extern const char *dlerror(void);
extern int printf(const char *format, ...);
extern int fflush(void *stream);
extern void _exit(int status) __attribute__((noreturn));
extern int strcmp(const char *left, const char *right);
extern size_t strlen(const char *text);
extern int snprintf(char *buffer, size_t capacity, const char *format, ...);
extern void *fopen(const char *name, const char *mode);
extern int fclose(void *file);
extern int fscanf(void *file, const char *format, ...);
extern int fseek(void *file, long offset, int origin);
extern long ftell(void *file);
extern size_t fread(void *buffer, size_t size, size_t count, void *file);
extern void *malloc(size_t size);
extern void free(void *address);
extern int bind_unity_services(void *il2cpp);
extern int bind_localization_resource(void *il2cpp, void *config);

void *(*get_class)(const void *, const char *, const char *);
static const void *(*get_method)(void *, const char *, int);
static void *(*new_object)(void *);
static void *(*invoke)(const void *, void *, void **, void **);
static void *(*invoke_boxed)(const void *, void *, void **, int, void **);
static void (*format_exception)(void *, char *, int);
static void (*format_stack_trace)(void *, char *, int);
static void (*native_stack_trace)(void *, unsigned long **, int *, const char **, const char **);
static void (*free_runtime)(void *);
static const void *get_inner_exception;
static int unity_exception_seen;

#ifdef ANDROID_BRIDGE
/* Bionic requires 64-byte alignment for the executable's TLS segment. */
_Alignas(64) __attribute__((used)) static _Thread_local int android_bridge_tls;
#endif

static const char *battle_directory = "/game";

static int set_battle_directory(const char *directory) {
    if (directory[0] != '/' || strlen(directory) > 180) {
        return 0;
    }
    battle_directory = directory;
    return 1;
}

void build_battle_path(char path[BATTLE_PATH_CAPACITY], const char *relative) {
    snprintf(path, BATTLE_PATH_CAPACITY, "%s/%s", battle_directory, relative);
}

const char *battle_path(const char *relative) {
    static char path[BATTLE_PATH_CAPACITY];
    build_battle_path(path, relative);
    return path;
}

__asm__(
    ".text\n"
    ".global _start\n"
    "_start:\n"
    "    mov x0, sp\n"
    "    sub sp, sp, #32\n"
    "    stp xzr, xzr, [sp]\n"
    "    stp xzr, xzr, [sp, #16]\n"
    "    mov x1, xzr\n"
    "    adr x2, main\n"
    "    mov x3, sp\n"
    "    bl __libc_init\n"
    "    brk #0\n"
);

static void report_native_stack(void *exception) {
    if (!native_stack_trace || !free_runtime) {
        return;
    }
    unsigned long *addresses = 0;
    int length = 0;
    const char *image_id = 0, *image_name = 0;
    native_stack_trace(exception, &addresses, &length, &image_id, &image_name);
    struct library_address runtime;
    if (addresses && dladdr((void *) invoke, &runtime)) {
        for (int i = 0; i < length && i < 30; ++i) {
            struct library_address frame;
            void *address = (void *) addresses[i];
            if (dladdr(address, &frame) && frame.base == runtime.base) {
                printf("REUSE managed_frame %d 0x%lx\n", i, addresses[i] - (unsigned long) frame.base);
            }
        }
    }
    free_runtime(addresses);
}

static void report_managed_error(const char *name, int arity, void *exception) {
    for (int depth = 0; exception && depth < 6; ++depth) {
        char description[384] = {0};
        format_exception(exception, description, sizeof(description));
        printf("REUSE managed_error %s/%d %d %s\n", name, arity, depth, description);
        if (format_stack_trace) {
            char stack[2048] = {0};
            format_stack_trace(exception, stack, sizeof(stack));
            if (*stack) {
                printf("%s\n", stack);
            }
        }
        report_native_stack(exception);
        void *diagnostic_error = 0;
        exception = get_inner_exception ? invoke(get_inner_exception, exception, 0, &diagnostic_error) : 0;
        if (diagnostic_error) {
            break;
        }
    }
    fflush(0);
}

void log_unity_exception(void *exception, void *context) {
    (void) context;
    report_managed_error("unity_async", 0, exception);
    __atomic_store_n(&unity_exception_seen, 1, __ATOMIC_RELEASE);
}

int unity_exception_logged(void) {
    return __atomic_load_n(&unity_exception_seen, __ATOMIC_ACQUIRE);
}

static int managed_call(void *klass, const char *name, int arity, void *instance, void **arguments,
                        void **result, int boxed) {
    const void *method = klass ? get_method(klass, name, arity) : 0;
    if (!method) {
        printf("REUSE missing_method %s/%d\n", name, arity);
        return 0;
    }
    void *exception = 0;
    if (boxed && !invoke_boxed) {
        printf("REUSE missing boxed invoke export\n");
        return 0;
    }
    *result = boxed ? invoke_boxed(method, instance, arguments, arity, &exception)
                    : invoke(method, instance, arguments, &exception);
    if (exception) {
        report_managed_error(name, arity, exception);
        return 0;
    }
    return 1;
}

int call(void *klass, const char *name, int arity, void *instance, void **arguments, void **result) {
    return managed_call(klass, name, arity, instance, arguments, result, 0);
}

int call_boxed(void *klass, const char *name, int arity, void *instance, void **arguments, void **result) {
    return managed_call(klass, name, arity, instance, arguments, result, 1);
}

void *construct(void *klass, int arity, void **arguments) {
    void *object = klass ? new_object(klass) : 0;
    void *unused = 0;
    return object && call(klass, ".ctor", arity, object, arguments, &unused) ? object : 0;
}

static void *read_managed_text(void *library, const char *path) {
    void *(*string_new_len)(const char *, unsigned int) = dlsym(library, "il2cpp_string_new_len");
    if (!string_new_len) {
        return 0;
    }
    void *file = fopen(path, "rb");
    if (!file) {
        printf("REUSE text_open_failed %s\n", path);
        return 0;
    }
    long size = fseek(file, 0, 2) ? -1 : ftell(file);
    void *text = size > 0 && size <= 4L * 1024 * 1024 && !fseek(file, 0, 0) ? malloc(size) : 0;
    size_t copied = text ? fread(text, 1, size, file) : 0;
    fclose(file);
    void *result = copied && copied == (size_t) size ? string_new_len(text, (unsigned int) size) : 0;
    free(text);
    if (!result) {
        printf("REUSE text_read_failed %s\n", path);
    }
    return result;
}

void *read_managed_bytes(void *library, const char *path, long *length) {
    const void *(*get_corlib)(void) = dlsym(library, "il2cpp_get_corlib");
    void *(*array_new)(void *, size_t) = dlsym(library, "il2cpp_array_new");
    unsigned int (*array_header_size)(void) = dlsym(library, "il2cpp_array_object_header_size");
    if (!get_corlib || !array_new || !array_header_size) {
        return 0;
    }
    void *byte_class = get_class(get_corlib(), "System", "Byte");
    void *file = byte_class ? fopen(path, "rb") : 0;
    if (!file) {
        printf("REUSE database_open_failed %s\n", path);
        return 0;
    }
    long size = fseek(file, 0, 2) ? -1 : ftell(file);
    void *bytes = size > 0 && size <= 128L * 1024 * 1024 && !fseek(file, 0, 0)
        ? array_new(byte_class, (size_t) size) : 0;
    size_t copied = bytes ? fread((char *) bytes + array_header_size(), 1, (size_t) size, file) : 0;
    fclose(file);
    if (!bytes || copied != (size_t) size) {
        printf("REUSE database_read_failed %s %lu/%ld\n", path, copied, size);
        return 0;
    }
    *length = size;
    return bytes;
}

static void *read_stage_entity(void *library, const void *json_image, const void *corlib,
                               void *entity_class) {
    const void *(*class_type)(void *) = dlsym(library, "il2cpp_class_get_type");
    void *(*type_object)(const void *) = dlsym(library, "il2cpp_type_get_object");
    if (!json_image || !entity_class || !class_type || !type_object) {
        printf("REUSE missing stage deserializer export or type\n");
        return 0;
    }
    void *json = read_managed_text(library, battle_path("stage/StageEntity.json"));
    if (!json) {
        return 0;
    }

    void *string_reader_class = get_class(corlib, "System.IO", "StringReader");
    void *reader_class = get_class(json_image, "Newtonsoft.Json", "JsonTextReader");
    void *serializer_class = get_class(json_image, "Newtonsoft.Json", "JsonSerializer");
    void *resolver_class = get_class(json_image, "Newtonsoft.Json.Serialization", "DefaultContractResolver");
    void *text_args[] = {json};
    void *text_reader = construct(string_reader_class, 1, text_args);
    void *reader_args[] = {text_reader};
    void *reader = text_reader ? construct(reader_class, 1, reader_args) : 0;
    void *serializer = construct(serializer_class, 0, 0);
    void *resolver = construct(resolver_class, 0, 0);
    if (!reader || !serializer || !resolver) {
        printf("REUSE stage_deserializer_alloc_failed\n");
        return 0;
    }
    int field_flags = 0x34; /* Instance, Public and NonPublic */
    unsigned char ignore_serializable = 0;
    int missing_member_error = 1;
    void *flags_args[] = {&field_flags};
    void *serializable_args[] = {&ignore_serializable};
    void *missing_member_args[] = {&missing_member_error};
    void *resolver_args[] = {resolver};
    void *unused = 0;
    void *entity = 0;
    void *deserialize_args[] = {reader, type_object(class_type(entity_class))};
    if (!call(resolver_class, "set_DefaultMembersSearchFlags", 1, resolver, flags_args, &unused) ||
        !call(resolver_class, "set_IgnoreSerializableAttribute", 1, resolver, serializable_args, &unused) ||
        !call(serializer_class, "set_ContractResolver", 1, serializer, resolver_args, &unused) ||
        !call(serializer_class, "set_MissingMemberHandling", 1, serializer, missing_member_args, &unused) ||
        !deserialize_args[1] ||
        !call(serializer_class, "Deserialize", 2, serializer, deserialize_args, &entity)) {
        return 0;
    }
    return entity;
}

static int setup_localization(void *library, const void *ui_image) {
    void *(*get_field)(void *, const char *) = dlsym(library, "il2cpp_class_get_field_from_name");
    void (*set_field_value)(void *, void *, void *) = dlsym(library, "il2cpp_field_set_value");
    void (*set_static_field)(void *, void *) = dlsym(library, "il2cpp_field_static_set_value");
    void *(*string_new)(const char *) = dlsym(library, "il2cpp_string_new");
    const unsigned short *(*string_chars)(void *) = dlsym(library, "il2cpp_string_chars");
    int (*string_length)(void *) = dlsym(library, "il2cpp_string_length");
    if (!ui_image || !get_field || !set_field_value || !set_static_field ||
        !string_new || !string_chars || !string_length) {
        return 0;
    }
    void *config_class = get_class(ui_image, "Qua.UI", "I18nConfig");
    void *helper_class = get_class(ui_image, "Qua.UI", "I18nHelper");
    void *i18n_class = get_class(ui_image, "Qua.UI", "I18n");
    void *csv = read_managed_text(library, battle_path("stage/EnglishLocalization.csv"));
    if (!config_class || !helper_class || !i18n_class || !csv) {
        return 0;
    }
    void *config = new_object(config_class);
    void *helper = construct(helper_class, 0, 0);
    void *language_field = get_field(config_class, "_defaultLanguage");
    void *comparison_field = get_field(config_class, "_keyComparerType");
    void *initialized_field = get_field(helper_class, "_isInitialized");
    void *instance_field = get_field(helper_class, "_instance");
    if (!config || !helper || !language_field || !comparison_field ||
        !initialized_field || !instance_field || !bind_localization_resource(library, config)) {
        return 0;
    }
    /* Values from the original I18nConfig asset, checked by prepare_stage.py. */
    int language = 10;
    int comparison = 4;
    set_field_value(config, language_field, &language);
    set_field_value(config, comparison_field, &comparison);
    void *setup_args[] = {string_new("English"), csv, &comparison};
    void *unused = 0;
    if (!call(helper_class, "SetUpI18n", 3, helper, setup_args, &unused)) {
        return 0;
    }
    unsigned char initialized = 1;
    set_field_value(helper, initialized_field, &initialized);
    set_static_field(instance_field, helper);
    void *i18n = 0;
    void *translation = 0;
    void *key_args[] = {string_new("common.separator_slash")};
    if (!call(helper_class, "get_I18n", 0, 0, 0, &i18n) || !i18n ||
        !call(i18n_class, "Get", 1, i18n, key_args, &translation) || !translation ||
        string_length(translation) != 1 || string_chars(translation)[0] != '/') {
        return 0;
    }
    printf("REUSE original_localization_loaded English\n");
    return 1;
}

enum battle_load {
    LOAD_SCENE = 1, LOAD_ROSTER = 2, LOAD_PLAYER = 4, TRACE_NULLS = 8, LOAD_OPENING = 16, LOAD_STRIKES = 32,
    LOAD_EXCHANGE = 64, LOAD_PHASE = 128, LOAD_ENEMY_PHASE = 256, LOAD_STAGE_RUN = 512, LOAD_INTERACTIVE = 1024,
};

static int verify_scene(void *library, const struct battle_images *images, const void *corlib,
                        void *database, const struct stage_expectations *expected, void *unit_repository, unsigned int mode) {
    const void *domain = images->domain;
    const void *infrastructure = images->infrastructure;
    void *(*get_field)(void *, const char *) = dlsym(library, "il2cpp_class_get_field_from_name");
    void (*get_field_value)(void *, void *, void *) = dlsym(library, "il2cpp_field_get_value");
    void *(*object_class)(void *) = dlsym(library, "il2cpp_object_get_class");
    void *(*unbox)(void *) = dlsym(library, "il2cpp_object_unbox");
    size_t (*array_length)(void *) = dlsym(library, "il2cpp_array_length");
    unsigned int (*array_header_size)(void) = dlsym(library, "il2cpp_array_object_header_size");
    if (!domain || !get_field || !get_field_value || !object_class || !unbox ||
        !array_length || !array_header_size) {
        printf("REUSE missing scene export or assembly\n");
        return 10;
    }
    void *store_class = get_class(infrastructure, "Eternal.Infrastructure", "EternalMasterDataStore");
    void *repository_class = get_class(infrastructure, "Eternal.Infrastructure", "MasterObjectRepository");
    void *map_repository_class = get_class(domain, "Eternal.Domain", "MapConvertRepository");
    void *stage_id_class = get_class(domain, "Eternal.Domain", "StageId");
    void *entity_class = get_class(domain, "Eternal.Domain", "SerializableStageEntity");
    void *unused = 0;
    void *store = 0;
    if (!call(store_class, "InitializeMasterDataStore", 0, 0, 0, &unused) ||
        !call(store_class, "get_Instance", 0, 0, 0, &store) || !store) {
        return 10;
    }
    void *helper_field = get_field(store_class, "_helper");
    void *helper = 0;
    if (helper_field) {
        get_field_value(store, helper_field, &helper);
    }
    void *database_args[] = {database};
    if (!helper || !call(object_class(helper), "set_DB", 1, helper, database_args, &unused)) {
        return 10;
    }
    void *repository_args[] = {store};
    void *repository = construct(repository_class, 1, repository_args);
    void *map_repository_args[] = {repository};
    void *map_repository = repository ? construct(map_repository_class, 1, map_repository_args) : 0;
    long long key = expected->id;
    void *id_args[] = {&key};
    void *stage_id = construct(stage_id_class, 1, id_args);
    if (!repository || !map_repository || !stage_id) {
        return 10;
    }
    printf("REUSE master_repository_created\n");
    fflush(0);

    void *map_stage = 0;
    void *map_found = 0;
    void *map_id = 0;
    void *lookup_args[] = {stage_id, &map_stage};
    if (!call(repository_class, "TryGetMasterMapStage", 2, repository, lookup_args, &map_found) ||
        !map_found || !*(unsigned char *) unbox(map_found) || !map_stage ||
        !call(object_class(map_stage), "get_MapId", 0, map_stage, 0, &map_id) || !map_id) {
        return 11;
    }
    long long map_key = *(long long *) unbox(map_id);
    printf("REUSE master_map %lld\n", map_key);
    fflush(0);
    if (map_key != expected->map_id) {
        return 11;
    }

    void *entity = read_stage_entity(library, images->json, corlib, entity_class);
    if (!entity) {
        return 12;
    }
    printf("REUSE serialized_stage_entity_created\n");
    fflush(0);
    if (!bind_unity_services(library)) {
        printf("REUSE unity_services_bind_failed\n");
        return 13;
    }
    if (!setup_localization(library, images->ui)) {
        printf("REUSE localization_setup_failed\n");
        return 13;
    }
    fflush(0);
    void *scene = 0;
    void *convert_args[] = {map_stage, map_repository};
    if (!call(entity_class, "ConvertWithMaster", 2, entity, convert_args, &scene) || !scene) {
        return 13;
    }
    void *scene_class = object_class(scene);
    void *scene_id = 0;
    void *map_field = get_field(scene_class, "MapEntity");
    void *npc_field = get_field(scene_class, "NpcUnitEntityHashSet");
    void *script_field = get_field(scene_class, "ADVScriptGroup");
    void *condition_field = get_field(scene_class, "VictoryConditionPair");
    if (!map_field || !npc_field || !script_field || !condition_field) {
        return 13;
    }
    void *map = 0;
    void *npcs = 0;
    void *scripts = 0;
    void *conditions = 0;
    get_field_value(scene, map_field, &map);
    get_field_value(scene, npc_field, &npcs);
    get_field_value(scene, script_field, &scripts);
    get_field_value(scene, condition_field, &conditions);
    void *count = 0;
    if (!map || !npcs || !scripts || !conditions ||
        !call(scene_class, "GetRecordKey", 0, scene, 0, &scene_id) || !scene_id ||
        !call(object_class(npcs), "Count", 0, npcs, 0, &count) || !count) {
        return 13;
    }
    void *chips_field = get_field(object_class(map), "MapChipList");
    void *chips = 0;
    if (chips_field) {
        get_field_value(map, chips_field, &chips);
    }
    void *chip_array_field = chips ? get_field(object_class(chips), "_values") : 0;
    void *chip_array = 0;
    if (chip_array_field) {
        get_field_value(chips, chip_array_field, &chip_array);
    }
    size_t chip_capacity = chip_array ? array_length(chip_array) : 0;
    size_t chip_count = 0;
    if (chip_array) {
        void *const *tiles = (void *const *) ((const char *) chip_array + array_header_size());
        for (size_t i = 0; i < chip_capacity; ++i) {
            chip_count += tiles[i] != 0;
        }
    }
    long long scene_key = *(long long *) unbox(scene_id);
    int npc_count = *(int *) unbox(count);
    printf("REUSE scene_created %lld map %lld npcs %d tiles %lu\n", scene_key, map_key, npc_count, chip_count);
    if (scene_key != key || npc_count != expected->npc_count || chip_count != (size_t) expected->chip_count) {
        return 13;
    }
    if ((mode & TRACE_NULLS) && !trace_null_references((void *) invoke)) {
        printf("REUSE null_trace_unavailable\n");
        return 14;
    }
    enum battle_action_mode action = BATTLE_NO_ACTION;
    if (mode & LOAD_INTERACTIVE) {
        action = BATTLE_INTERACTIVE;
    } else if (mode & LOAD_STAGE_RUN) {
        action = BATTLE_STAGE_RUN;
    } else if (mode & LOAD_ENEMY_PHASE) {
        action = BATTLE_ENEMY_PHASE;
    } else if (mode & LOAD_PHASE) {
        action = BATTLE_PHASE;
    } else if (mode & LOAD_EXCHANGE) {
        action = BATTLE_EXCHANGE;
    } else if (mode & LOAD_STRIKES) {
        action = BATTLE_STRIKES;
    }
    return mode & LOAD_ROSTER
        ? verify_roster(library, images, unit_repository, repository, expected, stage_id, scene, map,
                        !!(mode & LOAD_PLAYER), !!(mode & LOAD_OPENING), action)
        : 0;
}

static int attach_mumu_master_tables(void *library, const struct battle_images *images, void *database) {
    void *sqlite = dlopen(battle_path("liblibsqlite3_sys.so"), 2);
    if (!sqlite) {
        printf("REUSE mumu_sqlite_library_failed %s\n", dlerror());
        return 0;
    }
    void *(*string_new)(const char *) = dlsym(library, "il2cpp_string_new");
    if (!string_new || !images->master_sqlite) {
        return 0;
    }
    void *options_class = get_class(images->master_sqlite, "MasterSqlite", "SqliteDatabaseOptions");
    void *database_class = get_class(images->infrastructure, "Eternal.Infrastructure.Tables", "SqliteDatabase");
    void *validator = get_class(images->infrastructure, "Eternal.Infrastructure", "MasterSqliteDatabaseValidator");
    void *key = 0, *unused = 0;
    unsigned char disabled = 0, enabled = 1;
    void *disabled_args[] = {&disabled};
    void *enabled_args[] = {&enabled};
    const char *tables[] = {
        "npc.db", "ai.db", "character.db", "setting.db", "unit.db", "reward.db", "reward_set.db",
    };
    void *databases[7] = {0};
    char table_path[64];
    for (int i = 0; i < 7; ++i) {
        void *options = construct(options_class, 0, 0);
        if (!options || !call(validator, "CreateEncryptionKey", 0, 0, 0, &key) || !key) {
            return 0;
        }
        void *key_args[] = {key};
        if (!call(options_class, "set_EncryptionKey", 1, options, key_args, &unused) ||
            !call(options_class, "set_ReadOnly", 1, options, enabled_args, &unused) ||
            !call(options_class, "set_CreateIfMissing", 1, options, disabled_args, &unused) ||
            !call(options_class, "set_EnableWriteAheadLogging", 1, options, disabled_args, &unused)) {
            return 0;
        }
        /* A relative path keeps SQLite's open call within MuMu's path limits. The
           launcher sets the working directory to the isolated battle directory. */
        snprintf(table_path, sizeof(table_path), "./%s", tables[i]);
        void *args[] = {string_new(table_path), options};
        databases[i] = construct(database_class, 2, args);
        if (!databases[i]) {
            printf("REUSE mumu_master_table_failed %s\n", tables[i]);
            return 0;
        }
    }
    void *klass = get_class(images->infrastructure, "Eternal.Infrastructure", "EternalMasterMemoryDatabase");
    if (!call(klass, "AttachSqliteDatabases", 7, database, databases, &unused)) {
        return 0;
    }
    printf("REUSE mumu_master_sqlite_ready\n");
    return 1;
}

static int verify_master(void *library, const struct battle_images *images, void *unit_repository,
                         unsigned int mode) {
    struct stage_expectations expected = {0};
    void *manifest = fopen(battle_path("stage/StageExpected.tsv"), "r");
    if (!manifest) {
        printf("REUSE stage_expectations_missing\n");
        return 6;
    }
    int fields = fscanf(manifest, "%lld %31s %lld %d %d %d %lld %lld %d %d %d", &expected.id, expected.resource,
                        &expected.map_id, &expected.npc_count, &expected.chip_count, &expected.initial_npcs,
                        &expected.tower_stage_id, &expected.invasion_id, &expected.invasion_buff_rate,
                        &expected.gimmick_count, &expected.initial_gimmicks);
    fclose(manifest);
    if ((fields != 6 && fields != 11) || expected.id <= 0 || expected.map_id <= 0 || expected.chip_count <= 0 ||
        expected.npc_count <= 0 || expected.npc_count > 1024 || expected.initial_npcs < 0 ||
        expected.initial_npcs > expected.npc_count || expected.gimmick_count < 0 || expected.gimmick_count > 1024 ||
        expected.initial_gimmicks < 0 || expected.initial_gimmicks > expected.gimmick_count) {
        printf("REUSE stage_expectations_invalid\n");
        return 6;
    }
    const void *infrastructure = images->infrastructure;
    const void *(*get_corlib)(void) = dlsym(library, "il2cpp_get_corlib");
    void *(*unbox)(void *) = dlsym(library, "il2cpp_object_unbox");
    const unsigned short *(*string_chars)(void *) = dlsym(library, "il2cpp_string_chars");
    int (*string_length)(void *) = dlsym(library, "il2cpp_string_length");
    unsigned char (*gc_is_disabled)(void) = dlsym(library, "il2cpp_gc_is_disabled");
    format_exception = dlsym(library, "il2cpp_format_exception");
    format_stack_trace = dlsym(library, "il2cpp_format_stack_trace");
    native_stack_trace = dlsym(library, "il2cpp_native_stack_trace");
    free_runtime = dlsym(library, "il2cpp_free");
    if (!infrastructure || !get_corlib ||
        !unbox || !string_chars || !string_length ||
        !gc_is_disabled || !format_exception) {
        printf("REUSE missing master database export or assembly\n");
        return 6;
    }
    int gc_enabled = !gc_is_disabled();
    printf("REUSE gc_enabled %d\n", gc_enabled);
    fflush(0);
    if (!gc_enabled) {
        return 6;
    }

    const void *corlib = get_corlib();
    void *exception_class = get_class(corlib, "System", "Exception");
    get_inner_exception = exception_class ? get_method(exception_class, "get_InnerException", 0) : 0;
    void *serializer_initializer = get_class(infrastructure, "Eternal.Infrastructure", "EternalMessagePackInitializer");
    void *database_class = get_class(infrastructure, "Eternal.Infrastructure", "EternalMasterMemoryDatabase");
    void *table_class = get_class(infrastructure, "Eternal.Infrastructure.Tables", "MStageTable");
    void *stage_class = get_class(infrastructure, "Eternal.Infrastructure", "MStage");
    if (!serializer_initializer || !database_class || !table_class || !stage_class) {
        printf("REUSE missing master database type\n");
        return 6;
    }
    long size = 0;
    void *bytes = read_managed_bytes(library, battle_path("stage/master_database.bytes"), &size);
    if (!bytes) {
        return 7;
    }
    printf("REUSE master_database_bytes %ld\n", size);
    fflush(0);

    void *unused = 0;
    if (!call(serializer_initializer, "_SetupMessagePackResolver", 0, 0, 0, &unused)) {
        return 8;
    }
    printf("REUSE master_resolver_initialized\n");
    void *database = new_object(database_class);
    unsigned char intern_strings = 1;
    int workers = 1;
    void *constructor_args[] = {bytes, &intern_strings, 0, &workers};
    if (!database) {
        printf("REUSE master_database_alloc_failed\n");
        return 8;
    }
    printf("REUSE master_database_ctor_enter\n");
    fflush(0);
    if (!call(database_class, ".ctor", 4, database, constructor_args, &unused)) {
        return 8;
    }
    printf("REUSE master_database_ctor_return\n");
    fflush(0);
    if (battle_client_28 && !attach_mumu_master_tables(library, images, database)) {
        return 8;
    }
    void *table = 0;
    void *stage = 0;
    void *stage_id = 0;
    void *resource_id = 0;
    long long key = expected.id;
    void *find_args[] = {&key};
    if (!call(database_class, "get_MStageTable", 0, database, 0, &table) || !table ||
        !call(table_class, "FindById", 1, table, find_args, &stage) || !stage ||
        !call(stage_class, "get_Id", 0, stage, 0, &stage_id) || !stage_id ||
        !call(stage_class, "get_ResourceId", 0, stage, 0, &resource_id) || !resource_id) {
        return 9;
    }
    long long id = *(long long *) unbox(stage_id);
    const unsigned short *characters = string_chars(resource_id);
    int length = string_length(resource_id);
    char resource[32] = {0};
    if (!characters || length <= 0 || length >= (int) sizeof(resource)) {
        return 9;
    }
    for (int i = 0; i < length; ++i) {
        if (characters[i] >= 128) {
            return 9;
        }
        resource[i] = (char) characters[i];
    }
    printf("REUSE master_stage %lld %s\n", id, resource);
    if (id != key || strcmp(resource, expected.resource)) {
        return 9;
    }
    return mode & LOAD_SCENE ? verify_scene(library, images, corlib, database, &expected, unit_repository, mode) : 0;
}

int battle_client_28;

int main(int argc, char **argv) {
    if (argc >= 3 && !set_battle_directory(argv[2])) {
        printf("REUSE invalid_battle_directory\n");
        return 1;
    }
    battle_client_28 = argc >= 4 && !strcmp(argv[3], "2.8.0");
    const char *path = battle_path("libil2cpp.so");
    printf("REUSE loading %s\n", path);
    fflush(0);
    void *library = dlopen(path, 2); /* RTLD_NOW */
    if (!library) {
        printf("REUSE dlopen_error %s\n", dlerror());
        return 1;
    }
    printf("REUSE dlopen_ok\n");
    fflush(0);
    if (argc < 2 || !strcmp(argv[1], "link")) {
        return 0;
    }

    void (*set_data_dir)(const char *) = dlsym(library, "il2cpp_set_data_dir");
    unsigned int (*initialize)(const char *) = dlsym(library, "il2cpp_init");
    void *(*get_domain)(void) = dlsym(library, "il2cpp_domain_get");
    const void **(*get_assemblies)(void *, size_t *) = dlsym(library, "il2cpp_domain_get_assemblies");
    void *(*get_image)(const void *) = dlsym(library, "il2cpp_assembly_get_image");
    const char *(*get_image_name)(const void *) = dlsym(library, "il2cpp_image_get_name");
    get_class = dlsym(library, "il2cpp_class_from_name");
    get_method = dlsym(library, "il2cpp_class_get_method_from_name");
    new_object = dlsym(library, "il2cpp_object_new");
    invoke = dlsym(library, "il2cpp_runtime_invoke");
    invoke_boxed = dlsym(library, "il2cpp_runtime_invoke_convert_args");
    if (!set_data_dir || !initialize || !get_domain || !get_assemblies || !get_image ||
        !get_image_name || !get_class || !get_method || !new_object || !invoke) {
        printf("REUSE missing IL2CPP export\n");
        return 2;
    }

    char managed_directory[BATTLE_PATH_CAPACITY];
    build_battle_path(managed_directory, "Data/Managed");
    set_data_dir(managed_directory);
    printf("REUSE init_enter\n");
    fflush(0);
    unsigned int status = initialize("BattleReuse");
    printf("REUSE init_return %u\n", status);
    fflush(0);
    if (!status) {
        return 3;
    }

    void *domain = get_domain();
    size_t count = 0;
    const void **assemblies = domain ? get_assemblies(domain, &count) : 0;
    printf("REUSE assemblies %lu\n", count);
    fflush(0);
    if (!assemblies || !count || count > 2000) {
        return 4;
    }
    int attack_use_case = 0;
    int snapshot_progress = !strcmp(argv[1], "snapshot-progress");
    int snapshot_mode = snapshot_progress || !strcmp(argv[1], "snapshot");
    void *unit_repository = 0;
    const void *memory_pack = 0;
    struct battle_images images = {0};
    for (size_t i = 0; i < count; i++) {
        void *image = get_image(assemblies[i]);
        const char *name = get_image_name(image);
        if (name && !strcmp(name, "Application.dll")) {
            images.application = image;
            if (!snapshot_mode) {
                void *klass = get_class(image, "Eternal.Application", "AttackUseCase");
                attack_use_case = klass && get_method(klass, "ExecuteAsync", 4);
            }
        }
        if (snapshot_mode && name && !strcmp(name, "MemoryPack.dll")) {
            memory_pack = image;
        }
        if (name && !strcmp(name, "MasterSqlite.dll")) {
            images.master_sqlite = image;
        }
        if (name && !strcmp(name, "Domain.dll")) {
            images.domain = image;
        }
        if (name && !strcmp(name, "Newtonsoft.Json.dll")) {
            images.json = image;
        }
        if (name && !strcmp(name, "quaunity-ui.Runtime.dll")) {
            images.ui = image;
        }
        if (name && !strcmp(name, "Presentation.dll")) {
            images.presentation = image;
        }
        if (name && !strcmp(name, "UniTask.dll")) {
            images.unitask = image;
        }
        if (name && !strcmp(name, "Uguiss.Runtime.dll")) {
            images.uguiss = image;
        }
        if (name && !strcmp(name, "Octo.dll")) {
            images.octo = image;
        }
        if (name && !strcmp(name, "Infrastructure.dll")) {
            images.infrastructure = image;
            if (snapshot_mode) {
                continue;
            }
            void *klass = get_class(image, "Eternal.Infrastructure", "InMemoryIngameUnitRepository");
            const void *constructor = klass ? get_method(klass, ".ctor", 0) : 0;
            const void *find_all = klass ? get_method(klass, "FindAll", 0) : 0;
            if (constructor && find_all) {
                void *repository = new_object(klass);
                void *exception = 0;
                if (repository) {
                    invoke(constructor, repository, 0, &exception);
                    if (!exception) {
                        if (invoke(find_all, repository, 0, &exception) && !exception) {
                            unit_repository = repository;
                        }
                    }
                }
            }
        }
    }
    if (snapshot_mode) {
        const void *(*get_corlib)(void) = dlsym(library, "il2cpp_get_corlib");
        void *exception = get_corlib ? get_class(get_corlib(), "System", "Exception") : 0;
        get_inner_exception = exception ? get_method(exception, "get_InnerException", 0) : 0;
        format_exception = dlsym(library, "il2cpp_format_exception");
        format_stack_trace = dlsym(library, "il2cpp_format_stack_trace");
        int exported = format_exception && (snapshot_progress ? export_emulator_progress(library, &images, memory_pack)
                                                              : export_emulator_snapshot(library, &images, memory_pack));
        fflush(0);
        /* IL2CPP still owns background threads in this short-lived process. */
        _exit(exported ? 0 : 24);
    }
    printf("REUSE battle_method_resolved %d\n", attack_use_case);
    printf("REUSE unit_repository_created %d\n", unit_repository != 0);
    if (!attack_use_case || !unit_repository) {
        return 5;
    }
    unsigned int mode = 0;
    if (!strcmp(argv[1], "scene")) {
        mode = LOAD_SCENE;
    } else if (!strcmp(argv[1], "roster") || !strcmp(argv[1], "roster-trace")) {
        mode = LOAD_SCENE | LOAD_ROSTER;
        if (!strcmp(argv[1], "roster-trace")) {
            mode |= TRACE_NULLS;
        }
    } else if (!strcmp(argv[1], "deployment") || !strcmp(argv[1], "deployment-trace")) {
        mode = LOAD_SCENE | LOAD_ROSTER | LOAD_PLAYER;
        if (!strcmp(argv[1], "deployment-trace")) {
            mode |= TRACE_NULLS;
        }
    } else if (!strcmp(argv[1], "opening") || !strcmp(argv[1], "opening-trace")) {
        mode = LOAD_SCENE | LOAD_ROSTER | LOAD_PLAYER | LOAD_OPENING;
        if (!strcmp(argv[1], "opening-trace")) {
            mode |= TRACE_NULLS;
        }
    } else if (!strcmp(argv[1], "strikes") || !strcmp(argv[1], "strikes-trace")) {
        mode = LOAD_SCENE | LOAD_ROSTER | LOAD_PLAYER | LOAD_OPENING | LOAD_STRIKES;
        if (!strcmp(argv[1], "strikes-trace")) {
            mode |= TRACE_NULLS;
        }
    } else if (!strcmp(argv[1], "exchange") || !strcmp(argv[1], "exchange-trace")) {
        mode = LOAD_SCENE | LOAD_ROSTER | LOAD_PLAYER | LOAD_OPENING | LOAD_EXCHANGE;
        if (!strcmp(argv[1], "exchange-trace")) {
            mode |= TRACE_NULLS;
        }
    } else if (!strcmp(argv[1], "phase") || !strcmp(argv[1], "phase-trace")) {
        mode = LOAD_SCENE | LOAD_ROSTER | LOAD_PLAYER | LOAD_OPENING | LOAD_PHASE;
        if (!strcmp(argv[1], "phase-trace")) {
            mode |= TRACE_NULLS;
        }
    } else if (!strcmp(argv[1], "enemy-phase") || !strcmp(argv[1], "enemy-phase-trace")) {
        mode = LOAD_SCENE | LOAD_ROSTER | LOAD_PLAYER | LOAD_OPENING | LOAD_ENEMY_PHASE;
        if (!strcmp(argv[1], "enemy-phase-trace")) {
            mode |= TRACE_NULLS;
        }
    } else if (!strcmp(argv[1], "stage-run") || !strcmp(argv[1], "stage-run-trace")) {
        mode = LOAD_SCENE | LOAD_ROSTER | LOAD_PLAYER | LOAD_OPENING | LOAD_STAGE_RUN;
        if (!strcmp(argv[1], "stage-run-trace")) {
            mode |= TRACE_NULLS;
        }
    } else if (!strcmp(argv[1], "interactive") || !strcmp(argv[1], "interactive-trace")) {
        mode = LOAD_SCENE | LOAD_ROSTER | LOAD_PLAYER | LOAD_OPENING | LOAD_INTERACTIVE;
        if (!strcmp(argv[1], "interactive-trace")) {
            mode |= TRACE_NULLS;
        }
    }
    if (mode || !strcmp(argv[1], "stage")) {
        return verify_master(library, &images, unit_repository, mode);
    }
    return 0;
}
