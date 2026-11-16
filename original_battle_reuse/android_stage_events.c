/* Play the stage's gameplay commands through the original ADV selector and command classes. */

#include "android_interactive.h"

extern void *dlsym(void *library, const char *name);
extern void *malloc(unsigned long size);
extern void free(void *memory);
extern int printf(const char *format, ...);
extern int strcmp(const char *left, const char *right);

enum {
    ADV_IF, ADV_ELSE, ADV_ENDIF, ADV_ACTION,
    ADV_BRANCH_LIMIT = 32,
};

static const struct {
    const char *name;
    const char *class_name;
    int role;
} stage_commands[] = {
    {"if", "ADVIfCommand", ADV_IF},
    {"else", "ADVElseCommand", ADV_ELSE},
    {"endif", "ADVEndIfCommand", ADV_ENDIF},
    {"unit_appear", "ADVAppearUnitCommand", ADV_ACTION},
    {"unit_appear_multiple", "ADVAppearMultipleUnitCommand", ADV_ACTION},
    {"disappear", "ADVDisappearUnitCommand", ADV_ACTION},
    {"unit_disappear_multiple", "ADVDisappearMultipleUnitCommand", ADV_ACTION},
    {"unit_move", "ADVMoveUnitCommand", ADV_ACTION},
    {"unit_set_direction", "ADVSetUnitDirectionCommand", ADV_ACTION},
    {"kill", "ADVKillUnitCommand", ADV_ACTION},
    {"add_flag", "ADVAddFlagCommand", ADV_ACTION},
    {"map_open_hidden_area", "ADVOpenHiddenAreaCommand", ADV_ACTION},
    {"map_close_hidden_area", "ADVCloseHiddenAreaCommand", ADV_ACTION},
};

static struct {
    const struct exchange_api *api;
    const struct player_loop *loop;
    void *(*allocate)(void *);
    const void *(*class_type)(void *);
    void *(*reflect_type)(const void *);
    void (*set_field)(void *, void *, void *);
    void *(*new_string)(const char *);
    void *(*new_script)(const unsigned short *, int);
    unsigned int (*root)(void *, int);
    void *repository;
    void *selector;
    void *engine;
    void *parser;
    int failed;
} playback;

static int invoke(void *object, const char *method, int arity, void **arguments, void **result) {
    return object && call(playback.api->object_class(object), method, arity, object, arguments, result);
}

static void *field(void *object, const char *name) {
    void *value = 0;
    void *member = playback.api->get_field(playback.api->object_class(object), name);
    if (member) {
        playback.api->get_field_value(object, member, &value);
    }
    return value;
}

static int store(void *object, const char *name, void *value) {
    void *member = object ? playback.api->get_field(playback.api->object_class(object), name) : 0;
    if (!member || !value) {
        printf("REUSE original_stage_event_binding_failed %s\n", name);
        return 0;
    }
    playback.set_field(object, member, value);
    return 1;
}

static int command_index(const unsigned short *line, int length) {
    if (length < 2 || *line != '[') {
        return -1;
    }
    for (unsigned int i = 0; i < sizeof(stage_commands) / sizeof(stage_commands[0]); ++i) {
        const char *name = stage_commands[i].name;
        int j = 1;
        while (j < length && *name && line[j] == (unsigned char) *name) {
            ++j;
            ++name;
        }
        if (!*name && j < length && (line[j] == ' ' || line[j] == '\t' || line[j] == ']')) {
            return i;
        }
    }
    return -1;
}

static void *gameplay_script(void *script) {
    /* Stage assets place each command on its own line. Only omit the client presentation commands.
       Uguiss still parses the original text and initializes each command's typed parameters. */
    const unsigned short *text = playback.api->string_chars(script);
    int length = playback.api->string_length(script);
    unsigned short *selected = malloc(((unsigned long) length + 1) * sizeof(*selected));
    if (!selected) {
        playback.failed = 1;
        return 0;
    }
    int count = 0, actions = 0, start = 0;
    while (start < length) {
        int end = start;
        while (end < length && text[end] != '\r' && text[end] != '\n') {
            ++end;
        }
        while (start < end && (text[start] == ' ' || text[start] == '\t' || text[start] == 0xfeff)) {
            ++start;
        }
        int index = command_index(text + start, end - start);
        if (index >= 0) {
            actions += stage_commands[index].role == ADV_ACTION;
            for (int i = start; i < end; ++i) {
                selected[count++] = text[i];
            }
            selected[count++] = '\n';
        }
        start = end + 1;
    }
    void *value = actions ? playback.new_script(selected, count) : 0;
    free(selected);
    if (actions && !value) {
        playback.failed = 1;
    }
    return value;
}

static int list_count(void *list, int *count) {
    void *boxed = 0;
    if (!invoke(list, "get_Count", 0, 0, &boxed) || !boxed) {
        return 0;
    }
    *count = *(const int *) playback.api->unbox(boxed);
    return 1;
}

static void *list_item(void *list, int index) {
    void *item = 0;
    void *arguments[] = {&index};
    return invoke(list, "get_Item", 1, arguments, &item) ? item : 0;
}

static int select_scripts(int event, void *key, unsigned char update, void **scripts) {
    int no_override = -1;
    void *result = 0;
    void *arguments[] = {key, &event, &update, &no_override, scripts};
    if (!invoke(playback.selector, "_TryGetPlayableScript", key ? 5 : 4,
                key ? arguments : arguments + 1, &result) || !result) {
        playback.failed = 1;
        return 0;
    }
    return *(const unsigned char *) playback.api->unbox(result);
}

static int read_branch(void *command, int *enabled) {
    void *condition = 0, *reverse = 0;
    void *parameter = field(command, "IsReverse");
    if (!invoke(command, "_CheckCondition", 0, 0, &condition) || !condition ||
        !invoke(parameter, "get_HasValue", 0, 0, &reverse) || !reverse) {
        return 0;
    }
    int invert = *(const unsigned char *) playback.api->unbox(reverse);
    if (invert) {
        if (!invoke(parameter, "get_Value", 0, 0, &reverse) || !reverse) {
            return 0;
        }
        invert = *(const unsigned char *) playback.api->unbox(reverse);
    }
    *enabled = (*(const unsigned char *) playback.api->unbox(condition) != 0) ^ (invert != 0);
    return 1;
}

static int execute_script(void *script, void *dto, void *cancellation) {
    void *text = gameplay_script(script);
    if (!text) {
        return !playback.failed;
    }
    void *commands = 0, *unused = 0;
    void *parse_args[] = {text};
    void *dto_args[] = {dto};
    int count = 0;
    if (!invoke(playback.parser, "Parse", 1, parse_args, &commands) || !list_count(commands, &count) || !count ||
        !invoke(playback.engine, "set_EventTriggerDto", 1, dto_args, &unused)) {
        return 0;
    }
    void *command_args[] = {commands};
    if (!invoke(playback.engine, "_InitializeCommand", 1, command_args, &unused)) {
        return 0;
    }
    int parent[ADV_BRANCH_LIMIT], depth = 0, enabled = 1;
    for (int i = 0; i < count; ++i) {
        void *command = list_item(commands, i), *task = 0;
        if (!command) {
            return 0;
        }
        const char *name = playback.api->class_name(playback.api->object_class(command));
        if (!strcmp(name, "ADVIfCommand")) {
            if (depth == ADV_BRANCH_LIMIT) {
                return 0;
            }
            parent[depth++] = enabled;
            if (enabled && !read_branch(command, &enabled)) {
                return 0;
            }
        } else if (!strcmp(name, "ADVElseCommand")) {
            if (!depth) {
                return 0;
            }
            enabled = parent[depth - 1] && !enabled;
        } else if (!strcmp(name, "ADVEndIfCommand")) {
            if (!depth) {
                return 0;
            }
            enabled = parent[--depth];
        } else if (enabled) {
            void *arguments[] = {&cancellation};
            printf("REUSE original_stage_event_command %s\n", name);
            if (!invoke(command, "ExecuteSkipAsync", 1, arguments, &task) ||
                !finish_task(playback.api, playback.loop, name, task, 0, 0)) {
                return 0;
            }
            stage_event_board_changed();
        }
    }
    return depth == 0;
}

struct headless_task play_stage_event(int event, void *dto, void *cancellation) {
    void *scripts = 0;
    if (!playback.failed && select_scripts(event, 0, 1, &scripts)) {
        int count = 0;
        if (!list_count(scripts, &count)) {
            playback.failed = 1;
        }
        for (int i = 0; i < count && !playback.failed; ++i) {
            void *entity = list_item(scripts, i);
            void *script = entity ? field(entity, "Script") : 0;
            if (!script || !execute_script(script, dto, cancellation)) {
                playback.failed = 1;
            }
        }
    }
    if (playback.failed) {
        printf("REUSE original_stage_event_failed event %d\n", event);
    }
    return (struct headless_task) {0};
}

int has_stage_event(int event, void *key) {
    void *scripts = 0;
    return !playback.failed && select_scripts(event, key, 0, &scripts);
}

int has_stage_event_flag(void *flag) {
    void *context = 0, *result = 0;
    void *arguments[] = {flag};
    if (!invoke(playback.repository, "Get", 0, 0, &context) ||
        !invoke(context, "HasFlag", 1, arguments, &result) || !result) {
        playback.failed = 1;
        return 0;
    }
    return *(const unsigned char *) playback.api->unbox(result);
}

int stage_event_playback_ok(void) {
    return !playback.failed;
}

static int register_commands(const struct battle_images *images) {
    void *setting = playback.allocate(get_class(images->uguiss, "Uguiss", "CommandSetting"));
    void *info_class = get_class(images->uguiss, "Uguiss", "CommandInfo");
    void *commands = 0, *unused = 0;
    if (!info_class || !store(playback.engine, "commandSetting", setting) ||
        !invoke(setting, "get_Commands", 0, 0, &commands) || !commands) {
        return 0;
    }
    for (unsigned int i = 0; i < sizeof(stage_commands) / sizeof(stage_commands[0]); ++i) {
        void *klass = get_class(images->presentation, "Eternal.Presentation", stage_commands[i].class_name);
        void *info = construct(info_class, 0, 0);
        void *type = klass ? playback.reflect_type(playback.class_type(klass)) : 0;
        void *arguments[] = {info};
        if (!store(info, "_name", playback.new_string(stage_commands[i].name)) || !store(info, "_type", type) ||
            !invoke(commands, "Add", 1, arguments, &unused)) {
            return 0;
        }
    }
    return 1;
}

int bind_stage_event_playback(void *library, const struct battle_images *images, const struct battle_session *session,
                              const struct battle_exchange *exchange, const struct combat_services *combat,
                              const struct attack_services *attack, const struct exchange_api *api,
                              const struct player_loop *loop) {
    playback.api = api;
    playback.loop = loop;
    playback.allocate = dlsym(library, "il2cpp_object_new");
    playback.class_type = dlsym(library, "il2cpp_class_get_type");
    playback.reflect_type = dlsym(library, "il2cpp_type_get_object");
    playback.set_field = dlsym(library, "il2cpp_field_set_value_object");
    playback.new_string = dlsym(library, "il2cpp_string_new");
    playback.new_script = dlsym(library, "il2cpp_string_new_utf16");
    playback.root = dlsym(library, "il2cpp_gchandle_new");
    if (!images->uguiss || !session->adv_scripts || !session->adv_conditions || !playback.allocate ||
        !playback.class_type || !playback.reflect_type || !playback.set_field || !playback.new_string ||
        !playback.new_script || !playback.root) {
        printf("REUSE original_stage_event_runtime_missing uguiss %d scripts %d conditions %d\n",
               images->uguiss != 0, session->adv_scripts != 0, session->adv_conditions != 0);
        return 0;
    }
    const char *space = "Eternal.Presentation";
    playback.repository = exchange->adv_repository;
    playback.selector = playback.allocate(get_class(images->presentation, space, "ADVPresentationService"));
    playback.engine = playback.allocate(get_class(images->presentation, space, "ADVEngine"));
    void *parser_args[] = {playback.engine};
    playback.parser = construct(get_class(images->uguiss, "Uguiss", "Parser"), 1, parser_args);
    void *context = construct(get_class(images->domain, "Eternal.Domain", "ADVContextEntity"), 0, 0);
    void *unit_checker = construct(get_class(images->presentation, space, "ADVPlayableUnitKeyConditionChecker"), 0, 0);
    void *count_checker = construct(get_class(images->presentation, space, "ADVPlayableEvaluateCountChecker"), 0, 0);
    void *factory_args[] = {
        exchange->view, session->unit_repository, session->context_repository, session->turn_repository,
        session->map_repository, session->master_repository, combat->attack, exchange->conditions,
        exchange->koma, session->trait_service, attack->buff_view,
    };
    void *resolver_args[] = {session->unit_repository, session->map_repository};
    void *factory = construct(get_class(images->application, "Eternal.Application", "MapADVCommandFactory"), 11,
                              factory_args);
    void *resolver = construct(get_class(images->application, "Eternal.Application", "ADVUnitResolver"), 2,
                               resolver_args);
    void *context_args[] = {context}, *unused = 0;
    if (!playback.selector || !playback.engine || !playback.parser || !context ||
        !invoke(playback.repository, "Save", 1, context_args, &unused) ||
        !store(playback.selector, "_scriptGroupEntity", session->adv_scripts) ||
        !store(playback.selector, "_battleConditionService", exchange->conditions) ||
        !store(playback.selector, "_advContextRepository", playback.repository) ||
        !store(playback.selector, "_unitKeyConditionChecker", unit_checker) ||
        !store(playback.selector, "_evaluateCountChecker", count_checker) ||
        !store(playback.engine, "_mapADVCommandFactory", factory) ||
        !store(playback.engine, "_advUnitResolver", resolver) ||
        !store(playback.engine, "_battleConditionService", exchange->conditions) ||
        !store(playback.engine, "_advConditionGroups", session->adv_conditions) ||
        !store(playback.engine, "_advContextRepository", playback.repository) ||
        !register_commands(images) || !bind_stage_event_hooks(library, images)) {
        return 0;
    }
    playback.root(playback.selector, 0);
    playback.root(playback.engine, 0);
    playback.root(playback.parser, 0);
    printf("REUSE original_stage_event_playback_bound\n");
    return 1;
}
