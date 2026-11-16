"""Original score preparation over an explicit initialization-time unit span."""
from .battle_score import BattleScoreCalculator
from .battle_sides import is_opposition,is_alive_on_map
from .original import i32
from .trait_factory import i64


def score_configuration(stage):
    tables=stage.master.tables
    weights={r['StageScoreEvaluationTypeIndex']:r['Percent'] for r in tables['m_stage_score_evaluation_percent']}
    return dict(base_score=stage.stage['BaseScore'],
        **{key:weights.get(n,0) for n,key in enumerate(('stage_clear_weight','overkill_weight','survivor_weight','kill_weight','damage_taken_weight'),1)},
        overkill_bonus_rates={r['OverPercent']:r['Percent'] for r in tables['m_stage_score_overkill_percent']},
        survival_bonus_rates={r['KilledCount']:r['Percent'] for r in tables['m_stage_score_survivor_percent']},
        damage_taken_bonus_rates={r['DamageTakenPercent']:r['Percent'] for r in tables['m_stage_score_damage_taken_percent']})


def prepare_calculator(units,configuration,*,battle_power,initial_max_hp):
    """0x5ef197c success path. Getters must refer to the preparation snapshot.

    Enemy existence is deliberately not filtered. Own means exact side 0,
    further filtered by existence 0/1, regardless of HP or AdvOnly status.
    """
    units=list(units);powers={};enemy_count=0;enemy_total=0;own_count=0;own_hp=0
    for unit in units:
        if not is_opposition(unit['side'],0) or unit['adv_only']:continue
        enemy_count=i32(enemy_count+1);power=i32(battle_power(unit))
        enemy_total=i64(enemy_total+power)
        if unit['id'] in powers:raise ValueError('Duplicate original score unit identity')
        powers[unit['id']]=power
    for unit in units:
        if i32(unit['side'])!=0 or not is_alive_on_map(unit['existence']):continue
        own_count=i32(own_count+1);own_hp=i64(own_hp+i32(initial_max_hp(unit)))
    return BattleScoreCalculator(**configuration,own_total_unit_count=own_count,own_total_initial_hp=own_hp,
        enemy_total_unit_count=enemy_count,enemy_total_battle_power=enemy_total,enemy_battle_power=powers)
