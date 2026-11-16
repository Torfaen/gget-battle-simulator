/* Bind the original Unity math methods and route Unity logs to the guest console. */

#include "android_runtime.h"
#include "android_interactive.h"

typedef unsigned long size_t;

extern int open(const char *name, int flags, ...);
extern int close(int descriptor);
extern int getpagesize(void);
extern void *dlopen(const char *path, int flags);
extern void *dlsym(void *handle, const char *name);
extern void *mmap(void *address, size_t size, int protection, int flags, int descriptor, long offset);
extern int munmap(void *address, size_t size);
extern int mprotect(void *address, size_t size, int protection);
extern int printf(const char *format, ...);
extern int fflush(void *stream);
extern int strcmp(const char *left, const char *right);

struct guest_time {
    long seconds;
    long nanoseconds;
};

struct unity_page {
    long address;
    long file_offset;
    int protection;
};

extern int clock_gettime(int clock, struct guest_time *time);
extern int nanosleep(const struct guest_time *requested, struct guest_time *remaining);

static struct guest_time last_frame;
static int frame_count;
static float frame_delta;
static float time_scale = 1.0f;
static _Alignas(16) unsigned int unity_random_state[4];

void snapshot_unity_random(unsigned int state[4], int restore) {
    for (int i = 0; i < 4; ++i) {
        if (restore) {
            unity_random_state[i] = state[i];
        } else {
            state[i] = unity_random_state[i];
        }
    }
}

static struct guest_time startup;
static const unsigned short *(*string_chars)(void *);
static int (*string_length)(void *);
static void *(*string_new)(const char *);
static void *(*handle_target)(unsigned int);
static unsigned int localization_config;

long long wallclock_milliseconds(void) {
    struct guest_time now;
    return clock_gettime(0, &now) ? 0 : now.seconds * 1000LL + now.nanoseconds / 1000000;
}

static float elapsed_since_startup(void) {
    struct guest_time now;
    if (clock_gettime(1, &now)) {
        return 0.0f;
    }
    return (float) ((now.seconds - startup.seconds) + (now.nanoseconds - startup.nanoseconds) / 1e9);
}

static int current_frame(void) {
    return frame_count;
}

static float elapsed_since_last_frame(void) {
    return frame_delta;
}

static float scaled_frame_delta(void) {
    return frame_delta * time_scale;
}

static float get_time_scale(void) {
    return time_scale;
}

static void set_time_scale(float scale) {
    time_scale = scale;
}

int bind_player_loop_clock(void *library) {
    void (*add_icall)(const char *, const void *) = dlsym(library, "il2cpp_add_internal_call");
    if (!add_icall || clock_gettime(1, &last_frame)) {
        return 0;
    }
    add_icall("UnityEngine.Time::get_frameCount", current_frame);
    add_icall("UnityEngine.Time::get_deltaTime", scaled_frame_delta);
    add_icall("UnityEngine.Time::get_unscaledDeltaTime", elapsed_since_last_frame);
    add_icall("UnityEngine.Time::get_timeScale", get_time_scale);
    add_icall("UnityEngine.Time::set_timeScale", set_time_scale);
    return 1;
}

int advance_player_loop_clock(void) {
    const struct guest_time interval = {0, 16666667};
    struct guest_time now;
    nanosleep(&interval, 0);
    if (clock_gettime(1, &now)) {
        return 0;
    }
    frame_delta = (float) ((now.seconds - last_frame.seconds) +
                           (now.nanoseconds - last_frame.nanoseconds) / 1e9);
    last_frame = now;
    ++frame_count;
    return 1;
}

static void *persistent_data_path(void) {
    return string_new(battle_path("save"));
}

static void *application_data_path(void) {
    return string_new(battle_path("Data"));
}

static void *application_version(void) {
    return string_new(battle_client_28 ? "2.8.0" : "2.7.0");
}

static void set_screen_sleep_timeout(int seconds) {
    (void) seconds;
}

static void *load_resource(void *path, void *type) {
    (void) type;
    static const char name[] = "I18nConfig";
    if (path && string_length(path) == sizeof(name) - 1) {
        const unsigned short *characters = string_chars(path);
        for (size_t i = 0; i < sizeof(name) - 1; ++i) {
            if (characters[i] != (unsigned short) name[i]) {
                return 0;
            }
        }
        return handle_target(localization_config);
    }
    return 0;
}

int bind_localization_resource(void *il2cpp, void *config) {
    unsigned int (*new_handle)(void *, unsigned char) = dlsym(il2cpp, "il2cpp_gchandle_new");
    void (*add_icall)(const char *, const void *) = dlsym(il2cpp, "il2cpp_add_internal_call");
    handle_target = dlsym(il2cpp, "il2cpp_gchandle_get_target");
    if (!config || !new_handle || !handle_target || !add_icall || !string_chars || !string_length) {
        return 0;
    }
    localization_config = new_handle(config, 0);
    add_icall("UnityEngine.ResourcesAPIInternal::Load", load_resource);
    return 1;
}

enum {
    PAGE_SIZE = 4096,
    READ = 1,
    WRITE = 2,
    EXECUTE = 4,
    PRIVATE = 2,
    FIXED = 0x10,
    ANONYMOUS = 0x20,
};

int replace_instructions(unsigned int *entry, const unsigned int *code, int count) {
    unsigned long start = (unsigned long) entry & ~(PAGE_SIZE - 1UL);
    unsigned long end = ((unsigned long) (entry + count) + PAGE_SIZE - 1) & ~(PAGE_SIZE - 1UL);
    if (mprotect((void *) start, end - start, READ | WRITE | EXECUTE)) {
        return 0;
    }
    for (int i = 0; i < count; ++i) {
        entry[i] = code[i];
        __asm__ volatile("dc cvau, %0\n dsb ish\n ic ivau, %0\n dsb ish\n isb" :: "r" (entry + i) : "memory");
    }
    return !mprotect((void *) start, end - start, READ | EXECUTE);
}

__attribute__((used)) static void *manual_attack_resume;
__attribute__((used)) static void *map_attack_resume;
__attribute__((used)) static void *enemy_camera_resume;

/* Preserve the original managed-call arguments, including the indirect result register. */
__attribute__((naked)) static void manual_attack_intercept(void) {
    __asm__ volatile(
        "sub sp, sp, #80\n"
        "stp x0, x1, [sp]\n"
        "stp x2, x3, [sp, #16]\n"
        "stp x4, x5, [sp, #32]\n"
        "stp x6, x7, [sp, #48]\n"
        "stp x8, x30, [sp, #64]\n"
        "mov x0, x1\n"
        "mov x1, x2\n"
        "mov x2, x3\n"
        "bl manual_choose_enemy_defender\n"
        "ldp x0, x1, [sp]\n"
        "ldp x2, x3, [sp, #16]\n"
        "ldp x4, x5, [sp, #32]\n"
        "ldp x6, x7, [sp, #48]\n"
        "ldp x8, x30, [sp, #64]\n"
        "add sp, sp, #80\n"
        "adrp x16, manual_attack_resume\n"
        "ldr x16, [x16, :lo12:manual_attack_resume]\n"
        "br x16\n");
}

__attribute__((naked)) static void map_attack_intercept(void) {
    __asm__ volatile(
        "sub sp, sp, #80\n"
        "stp x0, x1, [sp]\n"
        "stp x2, x3, [sp, #16]\n"
        "stp x4, x5, [sp, #32]\n"
        "stp x6, x7, [sp, #48]\n"
        "stp x8, x30, [sp, #64]\n"
        "mov x0, x3\n"
        "mov w1, w5\n"
        "mov w2, w6\n"
        "bl report_manual_map_hit\n"
        "ldp x0, x1, [sp]\n"
        "ldp x2, x3, [sp, #16]\n"
        "ldp x4, x5, [sp, #32]\n"
        "ldp x6, x7, [sp, #48]\n"
        "ldp x8, x30, [sp, #64]\n"
        "add sp, sp, #80\n"
        "adrp x16, map_attack_resume\n"
        "ldr x16, [x16, :lo12:map_attack_resume]\n"
        "br x16\n");
}

__attribute__((naked)) static void enemy_camera_intercept(void) {
    __asm__ volatile(
        "sub sp, sp, #80\n"
        "stp x0, x1, [sp]\n"
        "stp x2, x3, [sp, #16]\n"
        "stp x4, x5, [sp, #32]\n"
        "stp x6, x7, [sp, #48]\n"
        "stp x8, x30, [sp, #64]\n"
        "mov x0, x1\n"
        "bl report_enemy_camera\n"
        "ldp x0, x1, [sp]\n"
        "ldp x2, x3, [sp, #16]\n"
        "ldp x4, x5, [sp, #32]\n"
        "ldp x6, x7, [sp, #48]\n"
        "ldp x8, x30, [sp, #64]\n"
        "add sp, sp, #80\n"
        "adrp x16, enemy_camera_resume\n"
        "ldr x16, [x16, :lo12:enemy_camera_resume]\n"
        "br x16\n");
}

static int bind_intercept(void *library, void *klass, const char *name, int arity, unsigned long rva,
                          const unsigned int prologue[4], void (*intercept)(void), void **resume_entry) {
    const void *(*get_method)(void *, const char *, int) = dlsym(library, "il2cpp_class_get_method_from_name");
    struct library_address runtime;
    if (!get_method || !klass || !dladdr((void *) get_method, &runtime) || getpagesize() != PAGE_SIZE) {
        return 0;
    }
    const void *method = get_method(klass, name, arity);
    unsigned int *entry = method ? *(unsigned int * const *) method : 0;
    if (!entry || (unsigned long) ((char *) entry - (char *) runtime.base) != rva) {
        return 0;
    }
    for (int i = 0; i < 4; ++i) {
        if (entry[i] != prologue[i]) {
            return 0;
        }
    }
    unsigned int *resume = mmap(0, PAGE_SIZE, READ | WRITE, PRIVATE | ANONYMOUS, -1, 0);
    if ((void *) resume == (void *) -1L) {
        return 0;
    }
    unsigned long next = (unsigned long) (entry + 4);
    const unsigned int trampoline[] = {prologue[0], prologue[1], prologue[2], prologue[3],
        0x58000050, 0xd61f0200, (unsigned int) next, (unsigned int) (next >> 32)};
    if (!replace_instructions(resume, trampoline, 8)) {
        munmap(resume, PAGE_SIZE);
        return 0;
    }
    *resume_entry = resume;
    unsigned long target = (unsigned long) intercept;
    const unsigned int redirect[] = {0x58000050, 0xd61f0200, (unsigned int) target, (unsigned int) (target >> 32)};
    return replace_instructions(entry, redirect, 4);
}

int bind_manual_attack_intercept(void *library, const struct battle_images *images) {
    const unsigned int attack[] = {0xd106c3ff, 0xa9167bfd, 0xa91767fa, 0xa9185ff8};
    const unsigned int map[] = {0xa9ba7bfd, 0xa9016ffc, 0xa90267fa, 0xa9035ff8};
    return bind_intercept(library, get_class(images->application, "Eternal.Application", "AttackUseCase"),
                          "ExecuteAsync", 4, battle_client_28 ? 0x5c8ce74 : 0x5ab847c,
                          attack, manual_attack_intercept, &manual_attack_resume) &&
        bind_intercept(library, get_class(images->domain, "Eternal.Domain", "MapWeaponExecutor"),
                       "_Attack", 6, battle_client_28 ? 0x613144c : 0x5f4a07c,
                       map, map_attack_intercept, &map_attack_resume);
}

int bind_enemy_camera_intercept(void *library, const struct battle_images *images) {
    const unsigned int prologue[] = {0xd10703ff, 0xf900c3fd, 0xa9195ffe, 0xa91a57f6};
    return bind_intercept(library, get_class(images->application, "Eternal.Application", "OtherSideAutoState"),
                          "_MoveCameraToActionUnitAsync", 3, battle_client_28 ? 0x5be2ba4 : 0x5a10e8c, prologue,
                          enemy_camera_intercept, &enemy_camera_resume);
}

static int (*use_support_attack)(void *, const void *);
static int (*use_support_defense)(void *, const void *);

static int consume_support_attack(void *unit, const void *method) {
    int result = use_support_attack(unit, method);
    report_consumed_support(unit);
    return result;
}

static int consume_support_defense(void *unit, const void *method) {
    int result = use_support_defense(unit, method);
    report_consumed_support(unit);
    return result;
}

int bind_support_count_intercepts(void *library, const struct battle_images *images) {
    void *klass = get_class(images->domain, "Eternal.Domain", "IngameUnitEntity");
    const unsigned int prologue[] = {0xa9be57fe, 0xa9014ff4, 0xf9400008, 0xaa0003f3};
    return bind_intercept(library, klass, "UseSupportAttackAndCounter", 0,
                          battle_client_28 ? 0x633d2ac : 0x6144a6c, prologue,
                          (void (*)(void)) consume_support_attack, (void **) &use_support_attack) &&
        bind_intercept(library, klass, "UseSupportDefense", 0,
                       battle_client_28 ? 0x633d3dc : 0x6144b9c, prologue,
                       (void (*)(void)) consume_support_defense, (void **) &use_support_defense);
}

enum {
    ADV_DEAD = 4, ADV_WARNING_HP = 5, ADV_HISTORIC_MOMENT = 6, ADV_SECRET = 8,
    ADV_PHASE_CHANGE = 10, ADV_POST_BATTLE = 11, ADV_AREA_ARRIVAL = 12, ADV_EVENT_AREA = 13,
};

#define STAGE_EVENT(name, event) \
    static struct headless_task name(void *service, void *dto, void *cancellation) { \
        (void) service; \
        return play_stage_event(event, dto, cancellation); \
    }
STAGE_EVENT(stage_dead, ADV_DEAD)
STAGE_EVENT(stage_warning_hp, ADV_WARNING_HP)
STAGE_EVENT(stage_historic_moment, ADV_HISTORIC_MOMENT)
STAGE_EVENT(stage_secret, ADV_SECRET)
STAGE_EVENT(stage_phase_change, ADV_PHASE_CHANGE)
STAGE_EVENT(stage_post_battle, ADV_POST_BATTLE)
STAGE_EVENT(stage_area_arrival, ADV_AREA_ARRIVAL)
STAGE_EVENT(stage_event_area, ADV_EVENT_AREA)
#undef STAGE_EVENT

static int stage_has_dead(void *service, void *key) {
    (void) service;
    return has_stage_event(ADV_DEAD, key);
}

static int stage_has_warning_hp(void *service) {
    (void) service;
    return has_stage_event(ADV_WARNING_HP, 0);
}

static int stage_has_area_arrival(void *service, void *key) {
    (void) service;
    return has_stage_event(ADV_AREA_ARRIVAL, key);
}

static int stage_has_flag(void *service, void *flag) {
    (void) service;
    return has_stage_event_flag(flag);
}

static int redirect_stage_event(unsigned int *entry, const void *target) {
    unsigned long address = (unsigned long) target;
    const unsigned int jump[] = {0x58000050, 0xd61f0200, (unsigned int) address, (unsigned int) (address >> 32)};
    return replace_instructions(entry, jump, 4);
}

int bind_stage_event_hooks(void *library, const struct battle_images *images) {
    const void *(*get_method)(void *, const char *, int) = dlsym(library, "il2cpp_class_get_method_from_name");
    struct library_address runtime;
    void *klass = get_class(images->application, "Eternal.Application", "MasterLeagueAdvApplicationService");
    if (!get_method || !klass || !dladdr((void *) get_method, &runtime) || getpagesize() != PAGE_SIZE) {
        return 0;
    }
    const struct {
        const char *name;
        int arity;
        unsigned long rva, rva_28;
        const void *target;
        unsigned long stub, stub_28;
    } methods[] = {
        {"PlayDeadEventAsync", 2, 0x5dc8a18, 0x5fb28bc, stage_dead, 0, 0},
        {"PlayWarningHpEventAsync", 2, 0x5dc8a98, 0x5fb293c, stage_warning_hp, 0, 0},
        {"PlayHistoricMomentEventAsync", 2, 0x5dc8b54, 0x5fb29f8, stage_historic_moment, 0, 0},
        {"PlaySecretEventAsync", 2, 0x5dc8bac, 0x5fb2a50, stage_secret, 0, 0},
        {"PlayPhaseChangeEventAsync", 2, 0x5dc8c0c, 0x5fb2ab0, stage_phase_change, 0, 0},
        {"PlayPostBattleEventAsync", 2, 0x5dc89b8, 0x5fb285c, stage_post_battle, 0, 0},
        {"PlayAreaArrivalEventAsync", 2, 0x5dc8ccc, 0x5fb2b70, stage_area_arrival, 0, 0},
        {"PlayEventAreaEventAsync", 2, 0x5dc8d24, 0x5fb2bc8, stage_event_area, 0, 0},
        {"ExistDeadEvent", 1, 0x5dc8a10, 0x5fb28b4, stage_has_dead, 0x5dc8a18, 0x5fb28bc},
        {"ExistWarningHpEvent", 0, 0x5dc8a88, 0x5fb292c, stage_has_warning_hp, 0x5dc8a98, 0x5fb293c},
        {"ExistAreaArrivalEvent", 1, 0x5dc8cc4, 0x5fb2b68, stage_has_area_arrival, 0x5dc8ccc, 0x5fb2b70},
        {"HasFlag", 1, 0x5dc8d7c, 0x5fb2c20, stage_has_flag, 0x5dc8d24, 0x5fb2bc8},
    };
    for (unsigned int i = 0; i < sizeof(methods) / sizeof(methods[0]); ++i) {
        const void *method = get_method(klass, methods[i].name, methods[i].arity);
        unsigned int *entry = method ? *(unsigned int * const *) method : 0;
        unsigned long rva = battle_client_28 ? methods[i].rva_28 : methods[i].rva;
        unsigned long stub_rva = battle_client_28 ? methods[i].stub_28 : methods[i].stub;
        if (!entry || (unsigned long) ((char *) entry - (char *) runtime.base) != rva) {
            printf("REUSE original_stage_event_hook_mismatch %s\n", methods[i].name);
            return 0;
        }
        if (!stub_rva) {
            if (entry[0] != 0xf81e0ffe || entry[1] != 0xa9014ff4 ||
                !redirect_stage_event(entry, methods[i].target)) {
                return 0;
            }
            continue;
        }
        /* This stub has only two words. Branch into the unused body of a redirected async stub. */
        unsigned int *stub = (unsigned int *) ((char *) runtime.base + stub_rva) + 4;
        long delta = stub - entry;
        if (entry[0] != 0x2a1f03e0 || entry[1] != 0xd65f03c0 ||
            stub[-4] != 0x58000050 || !redirect_stage_event(stub, methods[i].target) ||
            !replace_instructions(entry, (unsigned int[]) {0x14000000 | ((unsigned int) delta & 0x3ffffff)}, 1)) {
            return 0;
        }
    }
    return 1;
}

static int omit_presentation_method(unsigned int *entry, int returns_task) {
    const unsigned int ret[] = {0xd65f03c0};
    /* A zero UniTask<T> is completed. This 24-byte result uses the x8 return buffer. */
    const unsigned int completed_task[] = {0xa9007d1f, 0xf900091f, ret[0]};
    return replace_instructions(entry, returns_task ? completed_task : ret, returns_task ? 3 : 1);
}

int bind_board_marker_output(void *library, const struct battle_images *images, void *view) {
    void *(*object_class)(void *) = dlsym(library, "il2cpp_object_get_class");
    const void *(*get_method)(void *, const char *, int) = dlsym(library, "il2cpp_class_get_method_from_name");
    void *interface = get_class(images->application, "Eternal.Application", "IIngameView");
    if (!object_class || !get_method || !interface || !view) {
        return 0;
    }
    /* Layout and interface slots from the pinned IL2CPP interface dispatch. */
    struct interface_offset { void *klass; int offset; };
    struct virtual_method { void *entry; const void *method; };
    char *klass = object_class(view);
    unsigned short count = *(unsigned short *) (klass + 0x12e);
    const struct interface_offset *offsets = *(const struct interface_offset **) (klass + 0xb0);
    const struct {
        const char *name;
        int arity, slot;
        void *output;
    } methods[] = {
        {"ShowAuraBuff", 1, 75, report_board_aura},
        {"ShowMasuMarker", 4, 78, report_board_marker},
        {"StartBlinkingKomaGauges", 3, 366, report_map_gauge},
        {"StopAllKomaBlink", 0, 369, reset_map_gauges},
    };
    for (unsigned short i = 0; i < count; ++i) {
        if (offsets[i].klass == interface) {
            struct virtual_method *table = (struct virtual_method *) (klass + 0x138) + offsets[i].offset;
            for (unsigned int j = 0; j < sizeof(methods) / sizeof(methods[0]); ++j) {
                const void *method = get_method(klass, methods[j].name, methods[j].arity);
                struct virtual_method slot = table[methods[j].slot];
                if (!method || slot.method != method ||
                    (slot.entry != *(void * const *) method && slot.entry != methods[j].output)) {
                    return 0;
                }
            }
            for (unsigned int j = 0; j < sizeof(methods) / sizeof(methods[0]); ++j) {
                table[methods[j].slot].entry = methods[j].output;
            }
            return 1;
        }
    }
    return 0;
}

static void *headless_cutin_path(void *resource) {
    (void) resource;
    return string_new("");
}

int bind_headless_presentation(void *library, const struct battle_images *images) {
    const void *(*get_method)(void *, const char *, int) = dlsym(library, "il2cpp_class_get_method_from_name");
    const void *(*return_type)(const void *) = dlsym(library, "il2cpp_method_get_return_type");
    void *(*class_from_type)(const void *) = dlsym(library, "il2cpp_class_from_type");
    const char *(*class_name)(void *) = dlsym(library, "il2cpp_class_get_name");
    int (*value_size)(void *, unsigned int *) = dlsym(library, "il2cpp_class_value_size");
    struct library_address runtime;
    if (!get_method || !return_type || !class_from_type || !class_name || !value_size ||
        !dladdr((void *) get_method, &runtime) || getpagesize() != PAGE_SIZE) {
        return 0;
    }
    static const struct {
        const char *class_name;
        const char *name;
        int arity, arity_28;
        unsigned long rva, rva_28;
        unsigned int first_word, first_word_28;
        int returns_task;
    } methods[] = {
        {"SoundService", "LoadCueSheetAsync", 3, 3, 0x5cd8a08, 0x5edb308, 0xd10183ff, 0xd10183ff, 1},
        {"SoundService", "LoadAssetBundleCueSheetAsync", 2, 2, 0x5cd8bec, 0x5edb4ec, 0xd10143ff, 0xd10143ff, 1},
        {"SoundService", "UnloadCueSheet", 1, 1, 0x5cd8dc4, 0x5edb6c4, 0xa9be57fe, 0xa9be57fe, 0},
        {"SoundService", "MarkUnloadCueSheet", 1, 1, 0x5cd8e54, 0x5edb754, 0xa9be57fe, 0xa9be57fe, 0},
        {"SoundService", "PlayBgm", 1, 1, 0x5cd8ee4, 0x5edb7e4, 0xa9be57fe, 0xa9be57fe, 0},
        {"SoundService", "PlaySE", 1, 1, 0x5cd9824, 0x5edc124, 0xa9be57fe, 0xa9be57fe, 2},
        {"BuffDebuffViewService", "_RefreshIcons", 0, 0, 0x5d368b4, 0x5f20094, 0xd10283ff, 0xd10283ff, 0},
        {"IngameBattleConditionEventPlayer", "PlayStartBattleConditionAsync", 2, 3,
         0x5d4c1f4, 0x5f37454, 0xd10243ff, 0xd10283ff, 3},
    };
    for (unsigned int i = 0; i < sizeof(methods) / sizeof(methods[0]); ++i) {
        void *klass = get_class(images->application, "Eternal.Application", methods[i].class_name);
        int arity = battle_client_28 ? methods[i].arity_28 : methods[i].arity;
        const void *method = klass ? get_method(klass, methods[i].name, arity) : 0;
        unsigned int *entry = method ? *(unsigned int * const *) method : 0;
        void *result_class = method ? class_from_type(return_type(method)) : 0;
        unsigned int alignment = 0;
        unsigned long rva = battle_client_28 ? methods[i].rva_28 : methods[i].rva;
        unsigned int first_word = battle_client_28 ? methods[i].first_word_28 : methods[i].first_word;
        if (!entry || (char *) entry - (char *) runtime.base != rva || *entry != first_word || !result_class ||
            (methods[i].returns_task == 3
                ? strcmp(class_name(result_class), "UniTask") || value_size(result_class, &alignment) != 16
                : methods[i].returns_task == 1
                ? strcmp(class_name(result_class), "UniTask`1") || value_size(result_class, &alignment) != 24
                : methods[i].returns_task == 2
                    ? strcmp(class_name(result_class), "SoundPlayback")
                    : strcmp(class_name(result_class), "Void")) ||
            !(methods[i].returns_task >= 2
                ? replace_instructions(entry, (unsigned int[]) {0xaa1f03e0, 0xaa1f03e1, 0xd65f03c0}, 3)
                : omit_presentation_method(entry, methods[i].returns_task))) {
            printf("REUSE headless_presentation_binding_failed %s.%s\n", methods[i].class_name, methods[i].name);
            return 0;
        }
    }
    if (battle_client_28) {
        void *klass = get_class(images->domain, "Eternal.Domain", "CharacterCutinResourceInfo");
        const void *method = klass ? get_method(klass, "GetMapBattleCutinPath", 0) : 0;
        unsigned int *entry = method ? *(unsigned int * const *) method : 0;
        /* The headless view has no cut-in assets or Unity Resources provider. */
        if (!entry || (char *) entry - (char *) runtime.base != 0x68f602c || *entry != 0xa9bc67fe ||
            !redirect_stage_event(entry, headless_cutin_path)) {
            return 0;
        }
    }
    printf("REUSE headless_audio_bound\n");
    printf("REUSE headless_buff_icons_bound\n");
    return 1;
}

void format_managed_text(void *message, char *text, unsigned long capacity) {
    size_t used = 0;
    int length = message ? string_length(message) : 0;
    const unsigned short *chars = message ? string_chars(message) : 0;
    for (int i = 0; i < length && used + 4 < capacity; ++i) {
        unsigned int code = chars[i];
        if (code >= 0xd800 && code <= 0xdbff && i + 1 < length &&
            chars[i + 1] >= 0xdc00 && chars[i + 1] <= 0xdfff) {
            code = 0x10000 + ((code - 0xd800) << 10) + (chars[++i] - 0xdc00);
        } else if (code >= 0xd800 && code <= 0xdfff) {
            code = 0xfffd;
        }
        if (code < 0x80) {
            text[used++] = code < 0x20 ? ' ' : (char) code;
        } else if (code < 0x800) {
            text[used++] = (char) (0xc0 | (code >> 6));
            text[used++] = (char) (0x80 | (code & 63));
        } else if (code < 0x10000) {
            text[used++] = (char) (0xe0 | (code >> 12));
            text[used++] = (char) (0x80 | ((code >> 6) & 63));
            text[used++] = (char) (0x80 | (code & 63));
        } else {
            text[used++] = (char) (0xf0 | (code >> 18));
            text[used++] = (char) (0x80 | ((code >> 12) & 63));
            text[used++] = (char) (0x80 | ((code >> 6) & 63));
            text[used++] = (char) (0x80 | (code & 63));
        }
    }
    text[used] = 0;
}

static void console_log(int type, int options, void *message, void *context) {
    (void) options;
    (void) context;
    char text[1536];
    format_managed_text(message, text, sizeof(text));
    printf("UNITY log %d %s\n", type, text);
    fflush(0);
}

static void report_battle_telemetry(void *self, void *message, const void *method) {
    (void) self;
    (void) method;
    console_log(3, 0, message, 0);
}

void *create_headless_inquiry_log(void *library, const void *application_image) {
    const void *(*get_method)(void *, const char *, int) = dlsym(library, "il2cpp_class_get_method_from_name");
    void *(*new_object)(void *) = dlsym(library, "il2cpp_object_new");
    void *klass = get_class(application_image, "Eternal.Application", "InquiryLogService");
    struct library_address runtime;
    if (!klass || !get_method || !new_object || !dladdr((void *) get_method, &runtime) || getpagesize() != PAGE_SIZE) {
        return 0;
    }
    const void *method = get_method(klass, "LogCrashlytics", 1);
    unsigned int *entry = method ? *(unsigned int * const *) method : 0;
    unsigned long rva = battle_client_28 ? 0x5b6d4b0 : 0x59a1944;
    unsigned int word2 = battle_client_28 ? 0xf0045cd5 : 0xd0044515;
    unsigned int word3 = battle_client_28 ? 0xf0041334 : 0xd003fcb4;
    if (!entry || (char *) entry - (char *) runtime.base != rva ||
        entry[0] != 0xa9be57fe || entry[1] != 0xa9014ff4 || entry[2] != word2 || entry[3] != word3) {
        return 0;
    }
    unsigned long target = (unsigned long) report_battle_telemetry;
    const unsigned int redirect[] = {0x58000050, 0xd61f0200, (unsigned int) target, (unsigned int) (target >> 32)};
    /* InquiryLogService has no instance state. Its constructor starts the telemetry SDK. */
    if (!replace_instructions(entry, redirect, 4)) {
        return 0;
    }
    printf("REUSE original_battle_telemetry_local\n");
    return new_object(klass);
}

int bind_unity_services(void *il2cpp) {
    static const struct unity_page pages_27[] = {
        {0x1cd000, 0x1cd000, READ},             /* Euler rotation jump table */
        {0x4f2000, 0x4ee000, READ | EXECUTE},   /* icall entry points */
        {0x4f3000, 0x4ef000, READ | EXECUTE},   /* Random.InitState and RandomRangeInt */
        {0x51a000, 0x516000, READ | EXECUTE},   /* integer range algorithm */
        {0x708000, 0x704000, READ | EXECUTE},   /* scripting random state accessor */
        {0xdd5000, 0xdd1000, READ | EXECUTE},   /* matrix TRS */
        {0xddb000, 0xdd7000, READ | EXECUTE},   /* Euler quaternion */
        {0xddc000, 0xdd8000, READ | EXECUTE},   /* quaternion matrix */
        {0x1565000, 0x1561000, READ | EXECUTE}, /* sincosf PLT */
    };
    static const struct unity_page pages_28[] = {
        {0x1cd000, 0x1cd000, READ},
        {0x4f2000, 0x4ee000, READ | EXECUTE},
        {0x4f3000, 0x4ef000, READ | EXECUTE},
        {0x51a000, 0x516000, READ | EXECUTE},
        {0x709000, 0x705000, READ | EXECUTE},
        {0xdd6000, 0xdd2000, READ | EXECUTE},
        {0xddb000, 0xdd7000, READ | EXECUTE},
        {0xddc000, 0xdd8000, READ | EXECUTE},
        {0x1565000, 0x1561000, READ | EXECUTE},
    };
    const struct unity_page *pages = battle_client_28 ? pages_28 : pages_27;
    const long math_got = 0x15bd000;
    const long random_global_page = battle_client_28 ? 0x1640000 : 0x163f000;
    const size_t span = random_global_page + PAGE_SIZE;
    const long from_euler = battle_client_28 ? 0x4f2670 : 0x4f2460;
    const long matrix_trs = battle_client_28 ? 0x4f232c : 0x4f211c;
    void (*add_icall)(const char *, const void *) = dlsym(il2cpp, "il2cpp_add_internal_call");
    string_chars = dlsym(il2cpp, "il2cpp_string_chars");
    string_length = dlsym(il2cpp, "il2cpp_string_length");
    string_new = dlsym(il2cpp, "il2cpp_string_new");
    void *libm = dlopen("libm.so", 2);
    void *sincos = libm ? dlsym(libm, "sincosf") : 0;
    if (!add_icall || !string_chars || !string_length || !string_new || !sincos ||
        getpagesize() != PAGE_SIZE || clock_gettime(1, &startup)) {
        return 0;
    }
    int file = open(battle_path("libunity.so"), 0);
    if (file < 0) {
        return 0;
    }
    char *base = mmap(0, span, 0, PRIVATE | ANONYMOUS, -1, 0);
    if (base == (void *) -1) {
        close(file);
        return 0;
    }
    /* kernel_runner.py checks the exact libunity build before staging these pages. */
    for (size_t i = 0; i < sizeof(pages_27) / sizeof(pages_27[0]); ++i) {
        if (mmap(base + pages[i].address, PAGE_SIZE, pages[i].protection,
                 PRIVATE | FIXED, file, pages[i].file_offset) == (void *) -1) {
            close(file);
            munmap(base, span);
            return 0;
        }
    }
    close(file);
    if (mprotect(base + math_got, PAGE_SIZE, READ | WRITE) ||
        mprotect(base + random_global_page, PAGE_SIZE, READ | WRITE)) {
        munmap(base, span);
        return 0;
    }
    *(void **) (base + (battle_client_28 ? 0x15bd988 : 0x15bd418)) = sincos;
    *(void **) (base + (battle_client_28 ? 0x1640560 : 0x163ffe0)) = unity_random_state;
    if (mprotect(base + math_got, PAGE_SIZE, READ)) {
        munmap(base, span);
        return 0;
    }

    void (*euler)(const float *, float *) = (void *) (base + from_euler);
    void (*trs)(const float *, const float *, const float *, float *) = (void *) (base + matrix_trs);
    float origin[] = {0, 0, 0};
    float scale[] = {1, 1, 1};
    float rotation[4];
    float matrix[16];
    euler(origin, rotation);
    trs(origin, rotation, scale, matrix);
    for (int i = 0; i < 16; ++i) {
        if (matrix[i] != (i % 5 == 0 ? 1.0f : 0.0f)) {
            munmap(base, span);
            return 0;
        }
    }
    add_icall("UnityEngine.Quaternion::Internal_FromEulerRad_Injected", base + from_euler);
    add_icall("UnityEngine.Matrix4x4::TRS_Injected", base + matrix_trs);
    printf("REUSE original_unity_math_bound 2\n");
    void (*seed_random)(int) = (void *) (base + (battle_client_28 ? 0x4f323c : 0x4f302c));
    int (*random_range)(int, int) = (void *) (base + (battle_client_28 ? 0x4f3314 : 0x4f3104));
    seed_random(1);
    int roll = random_range(0, 1000);
    seed_random(1);
    if (roll < 0 || roll >= 1000 || random_range(0, 1000) != roll) {
        munmap(base, span);
        return 0;
    }
    seed_random((int) wallclock_milliseconds());
    add_icall("UnityEngine.Random::InitState", seed_random);
    add_icall("UnityEngine.Random::RandomRangeInt", random_range);
    printf("REUSE original_unity_random_bound\n");
    add_icall("UnityEngine.DebugLogHandler::Internal_Log", console_log);
    add_icall("UnityEngine.DebugLogHandler::Internal_LogException", log_unity_exception);
    printf("REUSE unity_console_bound\n");
    add_icall("UnityEngine.Application::get_persistentDataPath", persistent_data_path);
    add_icall("UnityEngine.Application::get_dataPath", application_data_path);
    add_icall("UnityEngine.Application::get_version", application_version);
    add_icall("UnityEngine.Screen::set_sleepTimeout", set_screen_sleep_timeout);
    add_icall("UnityEngine.Time::get_realtimeSinceStartup", elapsed_since_startup);
    return 1;
}
