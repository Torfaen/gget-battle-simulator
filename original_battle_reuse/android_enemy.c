/* Construct the original AI services and the original other-side phase state machine. */

#include "android_runtime.h"

extern int printf(const char *format, ...);
extern int fflush(void *stream);

static void *application(const struct battle_images *images, const char *name, int arity, void **args) {
    return construct(get_class(images->application, "Eternal.Application", name), arity, args);
}

static void *domain(const struct battle_images *images, const char *name, int arity, void **args) {
    return construct(get_class(images->domain, "Eternal.Domain", name), arity, args);
}

int create_stage_phase_services(void *library, const struct battle_images *images, const struct battle_session *session,
                                const struct battle_exchange *exchange, const struct combat_services *combat,
                                const struct attack_services *attack, struct stage_phase_services *services) {
    void *units = session->unit_repository;
    void *map = session->map_repository;
    void *turns = session->turn_repository;
    void *context = session->context_repository;
    void *master = session->master_repository;
    void *traits = session->trait_service;
    void *step_args[] = {map, units, turns, traits};
    void *steps = domain(images, "StepUnitPossibleDataFactory", 4, step_args);
    void *support_weapons = domain(images, "SupportWeaponSelector", 0, 0);
    void *support_args[] = {
        combat->availability, combat->calculator, context, units, map, turns, traits, support_weapons, steps,
    };
    void *support = support_weapons && steps ? domain(images, "SupportActionService", 9, support_args) : 0;
    void *participant_args[] = {units};
    void *participation = domain(images, "BattleParticipationStatusFactory", 1, participant_args);
    void *target_args[] = {units, map};
    void *character_targets = domain(images, "CharacterSkillTargetSelector", 2, target_args);
    void *skill_targets = domain(images, "UnitSkillTargetSelector", 2, target_args);
    void *supporter_target_args[] = {units, map, traits};
    void *supporter_targets = domain(images, "SupporterSkillTargetSelector", 3, supporter_target_args);
    void *available_skill_args[] = {traits, turns, units, map, character_targets};
    void *available_skills = character_targets
        ? domain(images, "AvailableCharacterSkillService", 5, available_skill_args) : 0;
    void *character_skill_args[] = {traits};
    void *character_skills = domain(images, "CharacterSkillExecutor", 1, character_skill_args);
    void *skill_check_args[] = {combat->skills, skill_targets};
    void *skill_check = skill_targets ? domain(images, "DefaultUnitSkillBehaviorAIChecker", 2, skill_check_args) : 0;
    if (!support || !participation || !available_skills || !character_skills || !skill_check || !supporter_targets) {
        return 0;
    }

    void *map_executor_args[] = {combat->attack, combat->calculator, units, map, turns, traits, master};
    void *map_executor = domain(images, "MapWeaponExecutor", 7, map_executor_args);
    void *map_weapon_args[] = {units, map, turns, traits, map_executor};
    void *map_weapon = map_executor ? domain(images, "MapWeaponServiceCreator", 5, map_weapon_args) : 0;
    void *map_check_args[] = {master, map_weapon};
    void *map_check = map_weapon ? application(images, "MapWeaponBehaviorAIChecker", 2, map_check_args) : 0;
    void *transform_args[] = {units, map, master, turns};
    void *transform = application(images, "IngameTransformationConversionService", 4, transform_args);
    void *scoped_form_args[] = {attack->form_change, map, traits, transform, turns, steps};
    void *scoped_form = transform ? application(images, "ScopedFormChangeService", 6, scoped_form_args) : 0;
    void *follow = domain(images, "FollowUnitLastTargetSpecification", 1, participant_args);
    if (!map_check || !scoped_form || !follow) {
        return 0;
    }
    void *ai_args[] = {
        units, map, turns, context, master, combat->availability, available_skills, skill_check, map_check,
        combat->prediction_rule, support, scoped_form, follow,
    };
    void *ai = application(images, "IngameAIService", 13, ai_args);
    void *score_args[] = {exchange->score, context, units, master, turns};
    void *map_score = domain(images, "MapEventBattleScoreService", 5, score_args);
    void *score_view_args[] = {exchange->view, context, turns, exchange->config, map_score};
    void *map_score_view = map_score ? application(images, "MapEventTotalScoreDisplayService", 5, score_view_args) : 0;
    void *trait_visuals = construct(get_class(images->infrastructure, "Eternal.Infrastructure",
                                              "InMemoryOnDemandTraitVisualRepository"), 0, 0);
    if (!ai || !map_score_view || !trait_visuals) {
        return 0;
    }
    /* The original factory installs its prediction use case on IngameAIService.
       Slots follow the pinned IngameUseCaseFactory constructor declaration. */
    void *factory_args[87] = {
        [0] = exchange->view, [1] = combat->attack, [2] = attack->attack_adv, [3] = available_skills,
        [4] = combat->availability, [5] = exchange->score, [6] = character_skills,
        [7] = exchange->adv_repository, [12] = exchange->adv, [13] = combat->calculator,
        [14] = exchange->score_calculator, [15] = exchange->score_repository, [16] = exchange->save,
        [17] = context, [18] = exchange->memento, [19] = session->supporter_repository,
        [20] = session->trait_repository, [21] = units, [22] = map, [23] = master, [25] = ai,
        [26] = exchange->conditions, [27] = exchange->config_service, [28] = traits,
        [29] = transform, [30] = session->unit_number_sequencer, [31] = attack->capture,
        [32] = session->non_unit_trait_owners, [34] = session->random_repository, [35] = turns,
        [36] = exchange->config, [37] = session->user_group_formation_repository, [38] = session->user_info_repository,
        [40] = session->user_unit_repository, [41] = session->user_character_repository,
        [42] = session->user_supporter_repository,
        [45] = combat->kill_log, [46] = map_weapon, [49] = steps, [50] = support, [51] = supporter_targets,
        [52] = attack->buff_view, [53] = session->aura_service, [54] = exchange->user_application,
        [55] = map_score, [56] = session->date_time,
        [59] = attack->situation, [60] = attack->event_score, [61] = attack->order, [62] = character_targets,
        [63] = attack->movie, [67] = participation, [69] = attack->escape,
        [70] = session->reinforcements, [71] = exchange->koma, [73] = attack->form_change,
        [74] = attack->story_points, [76] = attack->movement, [78] = attack->live_commentary,
        [79] = trait_visuals, [80] = combat->skills, [81] = skill_targets, [83] = session->trait_condition_checker,
        [86] = combat->splash_damage,
    };
    void *factory = application(images, "IngameUseCaseFactory", battle_client_28 ? 87 : 86, factory_args);
    if (!factory) {
        return 0;
    }
    void *inquiry = create_headless_inquiry_log(library, images->application);
    if (!inquiry) {
        return 0;
    }
    void *state_args[] = {
        exchange->view, units, context, turns, exchange->config, factory, exchange->conditions, ai,
        attack->buff_view, exchange->config_service, map_score_view, exchange->memento, inquiry, map_weapon, participation,
    };
    void *state_factory = application(images, "OtherSidePhaseStateFactory", 15, state_args);
    if (!state_factory) {
        return 0;
    }
    *services = (struct stage_phase_services) {state_factory, factory, trait_visuals, map_score, steps};
    printf("REUSE original_other_side_phase_factory_ready\n");
    fflush(0);
    return 1;
}
