"""Native support-defense rule sourcing and candidate gate subset."""
from .ai_configuration import determine_ai_type

def compile_support_rule(master,row):
    preset_id=row.get('MapNpcAiPresetId',0);rule=row.get('AiSupportActionRuleTypeIndex',0)
    source=dict(kind='explicit',preset_id=preset_id)
    parameter=row.get('AiSupportActionRuleParameterValue','');pending=None
    preset=master.index.get('m_map_npc_ai_preset',{}).get(preset_id) if preset_id else None
    if preset_id and preset is None:
        rule=0;pending='missing NPC support preset'
    elif rule==0 and preset is not None:
        rule=preset.get('AiSupportActionRuleTypeIndex',0)
        parameter=preset.get('AiSupportActionRuleParameterValue','');source['kind']='preset'
    if type(rule) is not int or rule not in (0,1,2,3):
        rule=0;pending='unknown support action rule'
    if parameter is not None and not isinstance(parameter,str):
        parameter=None;pending='unsupported support target-name schema';rule=0
    return dict(rule_type=rule,target_npc_name=parameter or None,source=source,pending_reason=pending)

def attach_support_policies(env,stage):
    npcs={npc['placement']['Id']:npc for npc in stage.npcs}
    for unit in env.units.values():
        unit['support_ai_variants']=[]
        if unit['team']=='player':
            unit.pop('npc_unique_name',None)
            unit['support_own_side']=True
        else:
            unit['support_own_side']=False
            npc=npcs[unit['source_npc_id']]
            unit['npc_unique_name']=npc['placement'].get('NpcUniqueName')
            unit['support_ai_variants']=[dict(compile_support_rule(env.rules.master,row),ai_type=row['MapNpcAiTypeIndex']) for row in npc['ai']]
    env.support_policy_profile='native-defense-eligibility-subset'

def support_action_policy(env,unit,protected,*,defense=False):
    ai_type=determine_ai_type(env.moment_trigger)
    if unit.get('support_own_side',unit['team']=='player'):
        rule=dict(rule_type=2,target_npc_name=None,source=dict(kind='native-own-side'),pending_reason=None)
    else:
        rule=next((row for row in unit.get('support_ai_variants',[]) if row['ai_type']==ai_type),
                  dict(rule_type=0,target_npc_name=None,source=dict(kind='native-default'),pending_reason=None))
    matches=(rule['target_npc_name'] is None or rule['target_npc_name']==protected.get('npc_unique_name'))
    allowed_types=(2,3) if defense else (1,2,3)
    return dict(rule,ai_type=ai_type,allowed=rule['pending_reason'] is None and rule['rule_type'] in allowed_types and matches,
                target_name_matches=matches)


def support_defense_policy(env,unit,protected):
    return support_action_policy(env,unit,protected,defense=True)
