"""Connect native Normal dispatch to ordinary attack/defend/evade execution.

Predictions include explicitly imported barrier state; full original service DTO
details and barrier lifecycle remain pending; unsupported rule sets retain an explicit CounterOnly fallback reason.
"""
from .combat_primitives import normal_counter_policy, predicted_hp_damage
from .original import i32
from .ai_configuration import load_counter_overrides,select_counter_configuration,determine_ai_type,counter_configuration_for_type

RULE_NAMES = {0:'none',1:'normal',2:'always-evade',3:'always-defend',4:'counter-only',5:'normal-priority-defend'}


def mix_counter_rule(explicit, preset):
    """AISwitchDto.CreateMix 0x6720e68..0x6720ea4: zero inherits preset."""
    return explicit if explicit != 0 else preset


def decide_response(env, attacker, defender, weapon, *, support_defense_enabled=False):
    from .exchange import select_counter
    ai_type=determine_ai_type(env.moment_trigger)
    configuration=counter_configuration_for_type(defender,ai_type)
    rule,configuration_source = select_counter_configuration(defender,env.rules.kernel,configuration=configuration)
    ability = defender.get('scalar_corrections', {}).get('ActionInabilityCount', 0) > 0
    branch = 'action_inability' if ability else 'counter_service'
    inputs = None
    remaining_hp = None
    preemptive = None
    def evade_branch():
        return 'evade_service' if defender.get('can_evade',True) else 'none_service'
    def defend_branch():
        return 'defend_service' if support_defense_enabled or defender.get('can_defend',True) else evade_branch()
    if rule == 0:
        branch = 'none_service'  # Native None returns None without the ability check.
    elif not ability and rule in (2,3):
        branch = evade_branch() if rule==2 else defend_branch()
    if rule in (1,5) and not ability:
        ordinary = env.rules._calculate_attack(attacker, defender, weapon, None, prediction=True,
            action_type='Attack', opponent_action_type='Counter')
        defended = env.rules._calculate_attack(attacker, defender, weapon, None, prediction=True,
            action_type='Attack', opponent_action_type='Defense')
        remaining_hp = i32(defender['hp'] - (0 if support_defense_enabled else
            predicted_hp_damage(ordinary['damage'],ordinary.get('hp_damage',ordinary['damage']),defender.get('hp_barrier',0))))
        if remaining_hp < 1:
            preemptive = select_counter(env,attacker,defender,preemptive_only=True,remaining_hp=remaining_hp)
        inputs = dict(hp=defender['hp'], barrier=defender.get('hp_barrier',0),
            total_damage=ordinary['damage'], hp_damage=ordinary.get('hp_damage',ordinary['damage']),
            defended_total_damage=defended['damage'], defended_hp_damage=defended.get('hp_damage',defended['damage']),
            can_defend=defender.get('can_defend', True),
            manual_side=defender.get('counter_manual_side', defender['team'] == 'player'),
            support_defense_enabled=support_defense_enabled, preemptive_available=preemptive is not None)
        branch = normal_counter_policy(**inputs)
        if rule==5:
            branch = ('counter_service' if remaining_hp>=1 else
                      'preemptive_service' if preemptive is not None else defend_branch())
    if branch=='evade_service':branch=evade_branch()
    action_type = {'action_inability': 'ActionInability', 'defend_service': 'Defense',
                   'evade_service': 'Evasion','none_service':'NoAction'}.get(branch, 'Counter')
    selected = (preemptive if branch=='preemptive_service' else
                select_counter(env,attacker,defender,remaining_hp=remaining_hp)) if action_type=='Counter' else None
    if action_type == 'Counter' and selected is None:
        if rule==5:
            branch=defend_branch()
            action_type={'defend_service':'Defense','evade_service':'Evasion','none_service':'NoAction'}[branch]
        else:action_type = 'NoAction'
    return dict(weapon=selected, action_type=action_type,
                trace=dict(policy=RULE_NAMES.get(rule,'counter-only')+'-response-subset',
                    rule_type=rule, ai_type=ai_type, branch=branch, action_type=action_type,
                    weapon=selected['id'] if selected else None, prediction_inputs=inputs,
                    predicted_remaining_hp=remaining_hp,
                    configuration_source=configuration_source,
                    pending_rule_reason=configuration.get('counter_policy_pending')))


def compile_npc_counter_configuration(master,row):
    result=dict(counter_policy_pending=None,counter_policy_source=dict(kind='explicit'),
                counter_overrides=[],counter_override_set_id=0,counter_rule_type=4)
    rule=row['AiCounterRuleTypeIndex'];preset_id=row['MapNpcAiPresetId']
    preset=master.index['m_map_npc_ai_preset'].get(preset_id) if preset_id else None
    if preset_id and preset is None:
        result['counter_policy_pending']='missing NPC AI preset'
        return result
    if preset is not None:
        rule=mix_counter_rule(rule,preset['AiCounterRuleTypeIndex'])
        result['counter_policy_source']=dict(kind='preset' if row['AiCounterRuleTypeIndex']==0 else 'explicit',
            preset_id=preset_id,explicit_rule=row['AiCounterRuleTypeIndex'],preset_rule=preset['AiCounterRuleTypeIndex'])
    overrides,reason=load_counter_overrides(master,row['OverrideAiCounterRuleSetId'])
    if reason or rule not in RULE_NAMES:
        result['counter_policy_pending']=reason or 'unknown counter rule pending'
    else:
        result.update(counter_rule_type=rule,counter_overrides=overrides,
                      counter_override_set_id=row['OverrideAiCounterRuleSetId'])
        if row['OverrideAiCounterRuleSetId']:
            result['counter_policy_source'].update(override_set_id=row['OverrideAiCounterRuleSetId'],
                                                  override_count=len(overrides))
    return result


def attach_counter_policies(env, stage):
    npcs={npc['placement']['Id']:npc for npc in stage.npcs}
    for unit in env.units.values():
        definition=env.rules.master.index['m_unit'][unit['unit_id']]
        unit['can_defend']=definition['CanDefend'];unit['can_evade']=definition['CanEvade']
        unit['counter_manual_side']=unit['team']=='player'
        unit['counter_ai_variants']=[]
        if unit['team']=='player':
            # Player response remains an explicit automated training policy.
            configuration=dict(counter_rule_type=1,counter_policy_source=dict(kind='player-auto-normal'),
                               counter_overrides=[],counter_override_set_id=0,counter_policy_pending=None)
        else:
            rows=npcs[unit['source_npc_id']]['ai']
            configurations=[dict(compile_npc_counter_configuration(env.rules.master,row),
                                 ai_type=row['MapNpcAiTypeIndex']) for row in rows]
            configuration=next((row for row in configurations if row['ai_type']==1),
                dict(counter_rule_type=1,counter_policy_source=dict(kind='native-default'),
                     counter_overrides=[],counter_override_set_id=0,counter_policy_pending=None))
            unit['counter_ai_variants']=[row for row in configurations if row['ai_type']!=1]
        unit.update({key:value for key,value in configuration.items() if key!='ai_type'})
    env.counter_policy_profile='basic-counter-response-subset'
    from .support_ai import attach_support_policies
    attach_support_policies(env,stage)
