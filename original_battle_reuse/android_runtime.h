#ifndef BATTLE_REUSE_ANDROID_RUNTIME_H
#define BATTLE_REUSE_ANDROID_RUNTIME_H

/* Paths below this directory belong to the isolated battle process. */
#define BATTLE_PATH_CAPACITY 256
void build_battle_path(char path[BATTLE_PATH_CAPACITY], const char *relative);
const char *battle_path(const char *relative);
extern int battle_client_28;

struct battle_images {
    const void *application;
    const void *domain;
    const void *infrastructure;
    const void *json;
    const void *ui;
    const void *presentation;
    const void *unitask;
    const void *octo;
    const void *uguiss;
    const void *master_sqlite;
};

struct library_address {
    const char *filename;
    void *base;
    const char *symbol;
    void *symbol_address;
};

struct stage_expectations {
    long long id;
    char resource[32];
    long long map_id;
    int npc_count;
    int chip_count;
    int initial_npcs;
    long long tower_stage_id;
    long long invasion_id;
    int invasion_buff_rate;
    int gimmick_count;
    int initial_gimmicks;
};

enum { FIXTURE_PLAYER_UNITS = 10 };

struct battle_session {
    const struct stage_expectations *stage;
    int stage_terrain;
    int unit_count;
    void *stage_initializer;
    void *view;
    void *unit_repository;
    void *master_repository;
    void *map_repository;
    void *context_repository;
    void *turn_repository;
    void *random_repository;
    void *trait_repository;
    void *supporter_repository;
    void *user_data_store;
    void *user_info_repository;
    void *user_unit_repository;
    void *user_character_repository;
    void *user_group_formation_repository;
    void *user_supporter_repository;
    void *user_record_repository;
    void *option_parts_repository;
    void *trait_service;
    void *trait_condition_checker;
    void *non_unit_trait_owners;
    void *reinforcements;
    void *unit_number_sequencer;
    void *date_time;
    void *aura_service;
    void *adv_scripts;
    void *adv_conditions;
};

struct battle_stage_args {
    void *id;
    void *master;
    void *scene;
    void *reinforcements;
    int formation_type;
    long long formation_target;
    long long challenge_id;
};

struct combat_services {
    void *splash_damage;
    void *memento;
    void *can_escape;
    void *calculator;
    void *availability;
    void *kill_log;
    void *attack;
    void *skills;
    void *prediction_rule;
};

enum battle_action_mode {
    BATTLE_NO_ACTION, BATTLE_STRIKES, BATTLE_EXCHANGE, BATTLE_PHASE, BATTLE_ENEMY_PHASE, BATTLE_STAGE_RUN,
    BATTLE_INTERACTIVE,
};

struct battle_exchange {
    void *view;
    void *adv;
    void *adv_repository;
    void *config;
    void *config_service;
    void *user_application;
    void *conditions;
    void *koma;
    void *score;
    void *score_repository;
    void *score_calculator;
    void *memento;
    void *save;
};

struct attack_services {
    void *attack_adv;
    void *buff_view;
    void *capture;
    void *situation;
    void *event_score;
    void *order;
    void *live_commentary;
    void *movie;
    void *escape;
    void *form_change;
    void *story_points;
    void *movement;
};

struct stage_phase_services {
    void *other_side_factory;
    void *use_cases;
    void *trait_visuals;
    void *map_score;
    void *movement_options;
    void *own_start;
};

struct player_attack {
    void *attacker;
    void *target;
    long long weapon;
};

int dladdr(const void *address, struct library_address *information);
extern void *(*get_class)(const void *image, const char *space, const char *name);
int call(void *klass, const char *name, int arity, void *instance, void **arguments, void **result);
int call_boxed(void *klass, const char *name, int arity, void *instance, void **arguments, void **result);
void *construct(void *klass, int arity, void **arguments);
void *read_managed_bytes(void *library, const char *path, long *length);
int export_emulator_snapshot(void *library, const struct battle_images *images, const void *memory_pack);
int export_emulator_progress(void *library, const struct battle_images *images, const void *memory_pack);
int replace_instructions(unsigned int *entry, const unsigned int *code, int count);
long long wallclock_milliseconds(void);
int trace_null_references(const void *runtime_symbol);
void log_unity_exception(void *exception, void *context);
int unity_exception_logged(void);
void format_managed_text(void *message, char *text, unsigned long capacity);
int bind_headless_presentation(void *library, const struct battle_images *images);
int bind_board_marker_output(void *library, const struct battle_images *images, void *view);
void *create_headless_inquiry_log(void *library, const void *application_image);
int initialize_octo_catalog(void *library, const void *octo_image, const char *stage_resource);
int bind_player_loop_clock(void *library);
int advance_player_loop_clock(void);
void snapshot_unity_random(unsigned int state[4], int restore);
int initialize_battle_random(void *library, const struct battle_images *images, const struct battle_session *session);
int initialize_battle_context(void *library, const struct battle_images *images, struct battle_session *session,
                              const struct battle_stage_args *stage);
int open_battle_turn(void *library, const struct battle_images *images, struct battle_session *session);
int initialize_exchange(void *library, const struct battle_images *images, const struct battle_session *session,
                        struct battle_exchange *exchange);
int create_stage_phase_services(void *library, const struct battle_images *images, const struct battle_session *session,
                                const struct battle_exchange *exchange, const struct combat_services *combat,
                                const struct attack_services *attack, struct stage_phase_services *services);
int select_opening_attack(void *library, const struct battle_images *images, const struct battle_session *session,
                          const struct combat_services *combat, struct player_attack *attack);
int execute_exchange(void *library, const struct battle_images *images, const struct battle_session *session,
                     const struct battle_exchange *exchange, const struct combat_services *combat,
                     enum battle_action_mode action);
int run_battle_action(void *library, const struct battle_images *images, const struct battle_session *session,
                      enum battle_action_mode action);
int verify_roster(void *library, const struct battle_images *images, void *unit_repository,
                  void *master_repository, const struct stage_expectations *stage_expected,
                  void *stage_id, void *scene, void *map, int load_player,
                  int load_opening, enum battle_action_mode action);

#endif
