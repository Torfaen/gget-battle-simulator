#ifndef BATTLE_REUSE_ANDROID_INTERACTIVE_H
#define BATTLE_REUSE_ANDROID_INTERACTIVE_H

#include "android_runtime.h"

struct exchange_api {
    void *(*object_class)(void *);
    void *(*unbox)(void *);
    const unsigned short *(*string_chars)(void *);
    int (*string_length)(void *);
    void *(*get_field)(void *, const char *);
    void (*get_field_value)(void *, void *, void *);
    const char *(*class_name)(void *);
};

struct player_loop;
struct battle_checkpoint;

struct battle_history {
    struct battle_checkpoint *last;
    unsigned int count;
    int keep_open;
    int load_pending;
    int mumu_checkpoint_pending;
};

int bind_stage_event_playback(void *library, const struct battle_images *images, const struct battle_session *session,
                              const struct battle_exchange *exchange, const struct combat_services *combat,
                              const struct attack_services *attack, const struct exchange_api *api,
                              const struct player_loop *loop);
int stage_event_playback_ok(void);

struct headless_task {
    void *source;
    unsigned long token;
};

struct headless_task play_stage_event(int event, void *dto, void *cancellation);
int has_stage_event(int event, void *key);
int has_stage_event_flag(void *flag);
int bind_stage_event_hooks(void *library, const struct battle_images *images);
void stage_event_board_changed(void);

enum manual_phase_result { MANUAL_ERROR, MANUAL_PASS, MANUAL_FINISHED, MANUAL_QUIT, MANUAL_REWOUND };

int bind_manual_attack_intercept(void *library, const struct battle_images *images);
int bind_enemy_camera_intercept(void *library, const struct battle_images *images);
int bind_support_count_intercepts(void *library, const struct battle_images *images);
void report_enemy_camera(void *unit);
void emit_other_phase_board(void);
int set_manual_defense_context(void *library, const struct battle_images *images, const struct battle_session *session,
                              const struct battle_exchange *exchange, const struct exchange_api *api,
                              void *prediction, void *availability, void *factory);
void manual_choose_enemy_defender(void *attacker, void *target, long long weapon);
void report_manual_map_hit(void *target, int prediction, int count);

int finish_task(const struct exchange_api *api, const struct player_loop *loop, const char *name,
                void *task, void **result, void *state_machine);
int start_player_phase(const struct battle_session *session, const struct stage_phase_services *phases,
                       const struct exchange_api *api, const struct player_loop *loop, void *token, int turn, int side);
void clear_battle_history(struct battle_history *history);
enum manual_phase_result run_manual_phase(void *library, const struct battle_images *images,
                                          const struct battle_session *session, const struct battle_exchange *exchange,
                                          const struct combat_services *combat, const struct stage_phase_services *phases,
                                          const struct exchange_api *api, const struct player_loop *loop,
                                          void *token, int *turn, int side, struct battle_history *history, int settled);

void report_board_aura(void *view, void *dto_list, const void *method);
void report_board_marker(void *view, int marker, void *coords, int side, int animate, const void *method);
void report_consumed_support(void *unit);
void report_map_gauge(void *view, int number, void *hp, void *en, const void *method);
void reset_map_gauges(void *view, const void *method);

#endif
