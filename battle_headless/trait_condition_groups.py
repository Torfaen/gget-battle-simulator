"""Original condition-list grouping for numeric master selector sets.

The leaf predicate receives the same candidate for every row in a group.
HP-extremum candidate arbitration remains an explicit unsupported boundary.
"""
COUNTED_SELECTORS=('UnitTags','CharacterTags','UnitRoleTypes','UnitSeries','CharacterSeries')


def condition_groups(rows):
    groups={}
    for row in rows:groups.setdefault(row.get('GroupId'),[]).append(row)
    return tuple(tuple(group) for group in groups.values())


def check_or_condition(rows):
    """MasterTraitConditionList.CheckOrCondition, 0x6125848.

    Group IDs are opaque strings. The native special cases are null/empty and
    the literal '0', not a requirement that the string parse as a number.
    """
    groups=condition_groups(rows)
    if len(groups)!=1:return False
    group=groups[0]
    if not group or not group[0].get('GroupId') or group[0]['GroupId']=='0':return False
    count=0
    for row in group:
        for field in COUNTED_SELECTORS:
            tokens=row.get(field,'').split(',') if row.get(field) else []
            if any(not token.isdigit() for token in tokens):
                raise NotImplementedError('Compound condition selector expression is not ported')
            count+=len({int(token) for token in tokens})
    return count>1


def condition_list_matches(rows, predicate, *, target_battle_side=None):
    """_IsActive/_IsTarget plus _SatisfiesConditionGroup normal-return logic.

    Pass target_battle_side only for _IsTarget. Native side 7 is excluded
    before even the empty-condition success path.
    """
    if target_battle_side==7:return False
    if not rows:return True
    if any(row.get('HpType') for row in rows):
        raise NotImplementedError('HP-extremum candidate arbitration is not ported')
    use_or=check_or_condition(rows)
    return any((any if use_or else all)(predicate(row) for row in group)
               for group in condition_groups(rows))
