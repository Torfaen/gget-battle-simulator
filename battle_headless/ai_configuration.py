"""Native AI counter override predicates and supported master-row loading."""
import math
from .tension import current_tension
from .original import i32,div
from .native_priority_sort import priority_sort


def total_hp_percent(unit):
    # IngameUnitEntity._GetTotalHpAsPercent 0x6136d70. Integer additions and
    # multiplication wrap before float32 conversion; native rounds toward +inf.
    maximum=i32(unit['max_hp']+unit.get('max_hp_barrier',0))
    if maximum<1:return 0
    numerator=i32(i32(unit['hp']+unit.get('hp_barrier',0))*100)
    return min(2**31-1,max(-2**31,math.ceil(div(numerator,maximum))))


def load_counter_overrides(master,set_id):
    if not set_id:return [],None
    rows=[dict(row) for row in master.tables['m_ai_counter_rule_set_content']
          if row['AiCounterRuleSetId']==set_id]
    if not rows:return [],None  # Native range lookup returns Empty; base rule remains active.
    fields=('Priority','AiCounterRuleTypeIndex','TensionGteIndex','HpPercentGte')
    if any(any(type(row.get(key)) is not int for key in fields) for row in rows):
        return [],'unsupported NPC counter override schema'
    if any(row['AiCounterRuleTypeIndex'] not in range(6) for row in rows):
        return [],'unknown NPC counter override rule'
    # Native comparison b__32_0 is second.Priority.CompareTo(first.Priority).
    return priority_sort(rows),None


def determine_ai_type(moment_trigger):
    # IsAfterHistoricMoment = Exists && !ShouldBeCheckedCondition.
    return 2 if moment_trigger.get('exists',False) and (
        moment_trigger.get('is_success',False) or moment_trigger.get('is_failure',False)) else 1


def counter_configuration_for_type(unit,ai_type):
    if ai_type==1 or unit.get('counter_policy_source',{}).get('kind')=='player-auto-normal':return unit
    return next((row for row in unit.get('counter_ai_variants',[]) if row['ai_type']==ai_type),
                dict(counter_rule_type=1,counter_policy_source=dict(kind='native-default'),
                     counter_overrides=[],counter_policy_pending=None))


def select_counter_configuration(unit,kernel,*,configuration=None):
    configuration=unit if configuration is None else configuration
    rule=configuration.get('counter_rule_type',4)
    source=dict(configuration.get('counter_policy_source',{}))
    rows=configuration.get('counter_overrides',[])
    if not rows:return rule,source
    tension=current_tension(unit,kernel);percent=total_hp_percent(unit)
    source.update(override_set_id=configuration['counter_override_set_id'],
                  condition_tension=tension,condition_hp_percent=percent,matched_override=False)
    for row in rows:
        if tension>=i32(row['TensionGteIndex']) and percent>=i32(row['HpPercentGte']):
            source.update(kind='override',base_rule=rule,priority=row['Priority'],matched_override=True)
            return row['AiCounterRuleTypeIndex'],source
    return rule,source
