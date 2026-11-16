"""Original supporter growth, leader conditions and scalar registration profile.

Account environments register these sources before initial passive evaluation.
Supporter active skills remain separate from the permanent scalar profile.
"""
from copy import deepcopy
from .original import div, mul, round_up
from .combat_primitives import same_group

# MasterSupporterLeaderSkillTrait._DecideMasterTraitType, table 0x27a2eb4.
LEADER_FIELDS={1:'AttackRate',2:'AttackValue',3:'DefenseRate',4:'DefenseValue',
               5:'MaxHpRate',6:'MaxHpValue',7:'MaxEnRate',8:'MaxEnValue',
               9:'MobilityRate',10:'MobilityValue'}


def additional_parameter(maximum, growth_basis_points):
    # UserSupporterEntity.get_AdditionalHp/Attack -> GrowthTable.GetGrowthValue.
    return round_up(mul(maximum, div(growth_basis_points, 10000)))


def _one(rows, **keys):
    found=[r for r in rows if all(r.get(k)==v for k,v in keys.items())]
    if len(found)!=1:raise ValueError(f'Expected one supporter row: {keys}; found {len(found)}')
    return found[0]


def compile_supporter(master, supporter):
    tables=master.tables
    definition=_one(tables['m_supporter'],Id=supporter['id'])
    growth=_one(tables['m_supporter_growth'],Level=supporter['level'],LimitBreakStep=supporter['limit_break'])
    skills=_one(tables['m_supporter_limit_break_step_skill'],SupporterId=supporter['id'],
                LimitBreakStep=supporter['limit_break'])
    leader=_one(tables['m_supporter_leader_skill'],Id=skills['SupporterLeaderSkillId'])
    contents=[]
    for content in sorted((r for r in tables['m_supporter_leader_skill_content']
                           if r['SupporterLeaderSkillContentSetId']==leader['SupporterLeaderSkillContentSetId']),
                          key=lambda r:r['SortOrder']):
        traits=[deepcopy(_one(tables['m_supporter_leader_skill_trait_content'],Id=link['SupporterLeaderSkillTraitContentId']))
                for link in sorted((r for r in tables['m_supporter_leader_skill_trait']
                    if r['SupporterLeaderSkillTraitSetId']==content['SupporterLeaderSkillTraitSetId']),key=lambda r:r['SortOrder'])]
        contents.append(dict(source=deepcopy(content),traits=traits,conditions=[deepcopy(r)
            for r in tables['m_trait_condition'] if r['TraitConditionSetId']==content['TraitConditionSetId']]))
    rate=growth['ParameterCorrectionRateBasisPoint']
    return dict(growth=deepcopy(growth),additional_hp=additional_parameter(definition['MaxHpAdditionValue'],rate),
        additional_attack=additional_parameter(definition['MaxAttackAdditionValue'],rate),
        leader=deepcopy(leader),leader_contents=contents,active_skill_id=skills['SupporterActiveSkillId'],
        runtime_profile='growth-and-scalar-leader-v1',active_skill_execution=False)


def leader_uses_or(groups, predicate_count):
    """CheckOrCondition tests one named group and the total predicate count."""
    groups = set(groups)
    return len(groups) == 1 and bool(next(iter(groups))) and predicate_count > 1


def leader_matches(rows, tags, series, role):
    """Evaluate native leader predicates against one unit's master data."""
    use_or = leader_uses_or((group for group, *_ in rows),
                            sum(len(field) for row in rows for field in row[1:]))
    if use_or:
        return any(bool(unit_tags & tags or unit_series & series) or role in roles
                   for _, unit_tags, roles, unit_series in rows)
    return all(unit_tags <= tags and unit_series <= series and (not roles or role in roles)
               for _, unit_tags, roles, unit_series in rows)


def leader_condition(master, unit, rows):
    """Original leader AND/OR selection for numeric tag/series/role sets.

    CheckOrCondition selects OR for a single explicitly named group with more
    than one predicate value. Other lists use AND; all values of a tag/series
    set must then match, while role sets accept any listed role.
    """
    fields=['UnitTags','UnitSeries','UnitRoleTypes']
    ignored={'Id','TraitConditionSetId','GroupId','TargetTypes'}|set(fields)
    parsed=[]
    for row in rows:
        if row['TargetTypes'] not in ('','Owner','SameGroup'):return None
        if row['GroupId'] and (not row['GroupId'].isdigit() or row['GroupId']=='0'):return None
        if any(v for k,v in row.items() if k not in ignored):return None
        values={}
        for field in fields:
            tokens=row[field].split(',') if row[field] else []
            if any(not token.isdigit() for token in tokens):return None
            values[field]={int(token) for token in tokens}
        parsed.append((row['GroupId'], values['UnitTags'], values['UnitRoleTypes'], values['UnitSeries']))
    unit_id=unit['unit_id']
    tags={r['LineageId'] for r in master.tables['m_unit_lineage'] if r['UnitId']==unit_id}
    definition=master.index['m_unit'][unit_id]
    series={r['SeriesId'] for r in master.tables['m_series_set'] if r['SeriesSetId']==definition['SeriesSetId']}
    return leader_matches(parsed, tags, series, definition['RoleTypeIndex'])


def corrections_for_unit(master, unit, sources):
    """Register same-group flat additions and recipient-owned leader traits.

    Source type 8 has the extra OwnerUnitId == recipient.Id check in
    ExecuteAlwaysTraitOnBattle (0x5f870d4..0x5f87154). Thus a selected leader
    recipient does not broadcast its correction to the rest of its group.
    """
    applied=[];inactive=[];pending=[]
    for source in sources:
        if not same_group(unit.get('formation_group'),source['group']):continue
        definition=source['definition']
        for field,key in [('MaxHpValue','additional_hp'),('AttackValue','additional_attack')]:
            applied.append(dict(supporter=source['id'],kind='growth',field=field,value=definition[key]))
        for content in definition['leader_contents']:
            match=leader_condition(master,unit,content['conditions'])
            if match is None:
                pending.append(dict(supporter=source['id'],reason='leader condition expression not ported',
                                    source=deepcopy(content)));continue
            if not match:
                inactive.append(dict(supporter=source['id'],content=content['source']['SupporterLeaderSkillTraitSetId']));continue
            for trait in content['traits']:
                field=LEADER_FIELDS.get(trait['TraitTypeIndex'])
                if field is None:
                    pending.append(dict(supporter=source['id'],reason='unknown leader trait',source=deepcopy(trait)));continue
                applied.append(dict(supporter=source['id'],kind='leader',trait_id=trait['Id'],field=field,value=trait['Value'],
                    max_group=definition['leader']['IsUsingMaxTraitValue']))
    return applied,inactive,pending
