"""Shared account battle assembly, independent of learning interfaces."""
from .account import DEFAULT_SNAPSHOT, import_formation, build_account_env
from .stage import StageFixture, DEFAULT_STAGE


def make_account_factory(*, snapshot=DEFAULT_SNAPSHOT, stage_id=DEFAULT_STAGE):
    stage = StageFixture(stage_id=stage_id)
    formation = import_formation(snapshot, master=stage.master)

    def factory(seed=37):
        from .exchange import attach_counter_exchange
        from .battle_map import attach_battle_map
        from .character_skills import attach_character_skills
        from .stage_outcomes import attach_stage_outcomes
        from .support_execution import attach_support_exchange
        from .support_defense import attach_support_defense
        from .counter_policy import attach_counter_policies
        env = build_account_env(formation, stage, seed)[0]
        from .initial_trait_inventory import attach_initial_trait_inventory
        attach_initial_trait_inventory(env,stage,formation)
        attach_battle_map(env, stage)
        attach_counter_exchange(env)
        attach_character_skills(env, stage.master)
        attach_stage_outcomes(env, stage)
        attach_support_exchange(env)
        attach_support_defense(env)
        attach_counter_policies(env, stage)
        env.battle_baseline = dict(stage_id=stage.stage_id,
            master_sha256=stage.master.sha256, captured_at=formation['captured_at'],
            trait_source_time_basis='snapshot_capture', seed=seed)
        return env

    return factory
