"""Fixed-stage trait conditions over explicit owner/target/context services.

Condition evaluation is separate from correction registration. The caller
supplies repository order and original origin coordinates; no default-true
predicate substitutes for unsupported source fields.
"""
from dataclasses import dataclass
from .battle_sides import is_alive_on_map,is_opposition
from .combat_primitives import same_group
from .conditions import hp_threshold,en_threshold
from .trait_condition_groups import condition_list_matches
from .trait_consumption import is_active,is_set_enabled
from .trait_participation import ACTION_NAMES,map_battle_action,is_active_attacker,is_attack_target
from .tension import current_tension

WEAPON_ENUM={'None':0,'Physical':1,'Beam':2,'Special':3,'Almighty':4}
ATTACK_ENUM={'None':0,'Ranged':1,'Melee':2,'Awaken':3}
TENSION_ENUM={'None':0,'Normal':2,'High':3,'SuperHigh':4,'Supercharged':5}
TARGET_ORDER=('Enemy','Owner','SameGroup','AttackTarget','ActiveAttacker')
FIXED_STAGE_TRAIT_KINDS=frozenset({1,2,3,7,8,9,11,12,14,17,19,21,23,25,27,29,31,32,33,34,46,47,51,52})
FIXED_STAGE_WEAPON_PASSIVE_KINDS=frozenset({39,44,45,81,82})
SUPPORTED_FIELDS={'Id','TraitConditionSetId','GroupId','TargetTypes','UnitTags','UnitSeries','UnitRoleTypes',
    'TensionTypes','WeaponAttributeTypes','WeaponAttackAttributeTypes','MapBattleActionTypes',
    'HpRateLteThreshold','UseHpRateLteThreshold','HpRateGteThreshold','IsHpDamaged',
    'EnRateLteThreshold','EnRateGteThreshold','EnValueLteThreshold','EnValueGteThreshold'}


def _tokens(row,key):return tuple(row[key].split(',')) if row.get(key) else ()


def validate_condition_rows(rows):
    for row in rows:
        unknown=[key for key,value in row.items() if value and key not in SUPPORTED_FIELDS]
        if unknown:raise NotImplementedError(f'Original condition fields not connected: {unknown}')
        targets=_tokens(row,'TargetTypes')
        if not set(targets)<=set(TARGET_ORDER):raise NotImplementedError(f'Condition target kinds not connected: {targets}')
        for key in ('UnitTags','UnitSeries','UnitRoleTypes'):
            if any(not token.isdigit() for token in _tokens(row,key)):
                raise NotImplementedError('Compound numeric condition expression not connected')
        for key,values in (('TensionTypes',TENSION_ENUM),('WeaponAttributeTypes',WEAPON_ENUM),
                           ('WeaponAttackAttributeTypes',ATTACK_ENUM),('MapBattleActionTypes',set(ACTION_NAMES.values()))):
            if not set(_tokens(row,key))<=set(values):raise NotImplementedError(f'Unknown {key} enum')


def condition_row_matches(row,candidate):
    """Original leaf ordering for the fixed stage's nonempty predicates.

    candidate contains already-resolved target getters. HP/EN pairs are
    battle-start values and current buffed maxima, not necessarily current HP.
    Empty tag/series constraints pass; nonempty sets use intersection.
    """
    targets=_tokens(row,'TargetTypes')
    if targets and not any(candidate['relations'][kind] for kind in TARGET_ORDER if kind in targets):return False
    roles={int(v) for v in _tokens(row,'UnitRoleTypes')}
    if roles and candidate['role'] not in roles:return False
    tensions={TENSION_ENUM[v] for v in _tokens(row,'TensionTypes')}
    if tensions and candidate['tension'] not in tensions:return False
    for field,key in (('UnitSeries','series'),('UnitTags','tags')):
        required={int(v) for v in _tokens(row,field)}
        if required and not required.intersection(candidate[key]):return False
    for field,key,enums in (('WeaponAttributeTypes','weapon_attributes',WEAPON_ENUM),
                            ('WeaponAttackAttributeTypes','attack_attributes',ATTACK_ENUM)):
        required={enums[v] for v in _tokens(row,field)}
        if required and not required.intersection(candidate[key]):return False
    lte=row.get('HpRateLteThreshold',0) if row.get('HpRateLteThreshold') or row.get('UseHpRateLteThreshold') else None
    if not hp_threshold(candidate['hp'],candidate['max_hp'],lte=lte,gte=row.get('HpRateGteThreshold') or None,
                        damaged=bool(row.get('IsHpDamaged'))):return False
    if not en_threshold(candidate['en'],candidate['max_en'],rate_lte=row.get('EnRateLteThreshold') or None,
        rate_gte=row.get('EnRateGteThreshold') or None,value_lte=row.get('EnValueLteThreshold') or None,
        value_gte=row.get('EnValueGteThreshold') or None):return False
    actions=set(_tokens(row,'MapBattleActionTypes'))
    return not actions or ACTION_NAMES[candidate['action']] in actions


@dataclass
class TraitExecutionContext:
    turn: int
    alive_units: tuple
    origin_coord: object


def exists_on_map(unit,participation=None):
    existence=unit.get('existence',unit.get('chance_step',{}).get('existence'))
    if existence is None:raise NotImplementedError('Explicit original unit existence required')
    return is_alive_on_map(existence) or (participation is not None and participation.contains(unit['id']))


def owner_on_map(owner,need_owner_on_map,participation=None):
    if not need_owner_on_map:return True
    if owner is None:return False
    if owner.get('non_unit_owner'):return True
    return exists_on_map(owner,participation)


def target_getters(master,trait,owner,unit,participation=None,weapon=None):
    definition=master.index['m_unit'][unit['unit_id']]
    owner_group=owner.get('formation_group') if owner is not None else None
    group=unit.get('formation_group')
    active=is_active_attacker(unit['id'],participation)
    if active and weapon is None:raise ValueError('The active attacker needs its original weapon')
    side=unit['battle_side'] if 'battle_side' in unit else group['side']
    return dict(side=side,
        relations=dict(Owner=unit['id']==trait['owner_unit_id'],
            Enemy=bool(group is not None and owner_group is not None and is_opposition(group['side'],owner_group['side'])),
            SameGroup=same_group(group,owner_group),ActiveAttacker=active,
            AttackTarget=is_attack_target(unit['id'],trait['owner_unit_id'],participation,
                owner_is_unit=owner is not None and not owner.get('non_unit_owner',False))),
        role=definition['RoleTypeIndex'],tension=current_tension(unit),
        tags={r['LineageId'] for r in master.tables['m_unit_lineage'] if r['UnitId']==unit['unit_id']},
        series={r['SeriesId'] for r in master.tables['m_series_set'] if r['SeriesSetId']==definition['SeriesSetId']},
        hp=unit['hp'] if unit.get('battle_start_hp') is None else unit['battle_start_hp'],max_hp=unit['max_hp'],
        en=unit['en'] if unit.get('battle_start_en') is None else unit['battle_start_en'],max_en=unit['max_en'],
        action=map_battle_action(unit['id'],participation),
        weapon_attributes=set(weapon['weapon_attributes']) if active else set(),
        attack_attributes=set(weapon['attack_attributes']) if active else set())


class TraitConditionChecker:
    """Original public gating and candidate scan with explicit dependencies.

    services: parent(trait_id), owner(owner_id,context,participation),
    need_owner_on_map(trait), rows(trait,active), candidate(unit,context,p),
    matches(trait,owner,candidate,rows,target_check), return_objects().
    """
    def __init__(self,services):self.services=services

    def _parent(self,trait):
        parent=self.services.parent(trait['runtime_trait_id'])
        if parent is None:raise KeyError('Original condition checker requires the parent trait set')
        return parent

    def active(self,trait,context,participation=None):
        if not is_active(trait):return False
        parent=self._parent(trait)
        if not is_set_enabled(parent):return False
        if trait['trait_type']==84 and any(t['trait_type']==83 and not is_active(t) for t in parent['values']):return False
        return self.active_conditions(trait,context,participation)

    def active_conditions(self,trait,context,participation=None):
        """Shared private scan; weapon checkables have no runtime set/counter gate."""
        s=self.services;owner=s.owner(trait['owner_unit_id'],context,participation)
        result=False
        if owner_on_map(owner,s.need_owner_on_map(trait),participation):
            rows=s.rows(trait,True)
            if not rows:result=True
            else:
                for unit in context.alive_units:
                    coord=context.origin_coord(unit)
                    if coord is None:continue
                    candidate=s.candidate(unit,context,participation)
                    if not owner_on_map(unit,s.need_owner_on_map(trait),participation):continue
                    if s.matches(trait,owner,candidate,rows,False):result=True;break
        s.return_objects()
        return result

    def target(self,trait,unit,context,participation=None):
        if not is_set_enabled(self._parent(trait)):return False
        s=self.services;owner=s.owner(trait['owner_unit_id'],context,participation)
        result=False
        if owner_on_map(owner,s.need_owner_on_map(trait),participation):
            # Unlike the active scan, native does not skip a missing target
            # origin or run the target's ExistsOnMap gate before _IsTarget.
            context.origin_coord(unit)
            candidate=s.candidate(unit,context,participation)
            result=s.matches(trait,owner,candidate,s.rows(trait,False),True)
        s.return_objects()
        return result


def master_condition_rows(master,trait,active):
    if 'supporter_definition' in trait:
        definition=trait['supporter_definition']
        if active:return definition['active_conditions']
        return definition.get('target_conditions',[dict(TargetTypes=definition.get('target',''))])
    from .weapon_corrections import _conditions
    identity=trait['master_trait']['ActiveConditionSetId' if active else 'TargetConditionSetId']
    rows=_conditions(master,identity) if identity else []
    if identity and not rows:raise KeyError(f'Missing original condition set {identity}')
    return rows


class MasterTraitConditionServices:
    """Connect fixed-stage master rows to explicit unit and owner repositories.

    The caller supplies original context coordinates and participation. This
    service only evaluates conditions; it does not apply effects, compute
    boosts or create/refresh battle snapshots. Diagnostic traces are optional.
    """
    def __init__(self,master,groups,units,non_unit_owners,*,weapon=None,trace=None):
        self.master=master;self.units=units;self.weapon=weapon;self.trace=trace
        self.parents={};self.condition_rows={}
        self.non_unit_owners={identity:dict(owner,id=identity,non_unit_owner=True)
                              for identity,owner in non_unit_owners.items()}
        for group in groups.values():
            for trait in group['values']:
                if trait['trait_type'] not in FIXED_STAGE_TRAIT_KINDS|FIXED_STAGE_WEAPON_PASSIVE_KINDS:
                    raise NotImplementedError('Condition service requires a verified fixed-stage trait class')
                identity=trait['runtime_trait_id']
                if identity in self.parents:raise ValueError('Duplicate runtime trait identity')
                self.parents[identity]=group
                for active in (False,True):
                    rows=master_condition_rows(master,trait,active)
                    validate_condition_rows(rows)
                    self.condition_rows[identity,active]=rows

    def parent(self,identity):return self.parents.get(identity)

    def owner(self,identity,context,participation):
        # _CreateOwner checks the non-unit repository before the unit repository.
        return self.non_unit_owners.get(identity,self.units.get(identity))

    def need_owner_on_map(self,trait):
        # Domain.dll has only these four false overrides of IngameTrait's true.
        return trait['trait_type'] not in (31,32,33,34)

    def rows(self,trait,active):return self.condition_rows[trait['runtime_trait_id'],active]
    def candidate(self,unit,context,participation):return (unit,participation)
    def return_objects(self):pass  # Plain values do not use the native object pool.

    def matches(self,trait,owner,candidate,rows,target_check):
        unit,participation=candidate
        side=unit['battle_side'] if 'battle_side' in unit else unit['formation_group']['side']
        visited=[]
        # Match the side/empty-list gates before resolving unused getters.
        if target_check and side==7:result=False
        elif not rows:result=True
        else:
            getters=target_getters(self.master,trait,owner,unit,participation,self.weapon)
            def predicate(row):
                matched=condition_row_matches(row,getters)
                visited.append(dict(condition_id=row.get('Id'),group_id=row.get('GroupId'),matches=matched))
                return matched
            result=condition_list_matches(rows,predicate,target_battle_side=side if target_check else None)
        if self.trace is not None:
            self.trace.append(dict(runtime_trait_id=trait['runtime_trait_id'],candidate_id=unit['id'],
                target_check=target_check,result=result,rows=visited))
        return result


def weapon_trait_condition_matches(master,units,non_unit_owners,owner_id,source,context,participation,weapon):
    """CheckableWeaponTrait -> shared private active scan (no parent-set gate)."""
    from types import SimpleNamespace
    from .weapon_corrections import _conditions
    identity=source['ApplyConditionSetId']
    rows=_conditions(master,identity) if identity else []
    if identity and not rows:raise KeyError(f'Missing weapon apply condition set {identity}')
    validate_condition_rows(rows)
    base=MasterTraitConditionServices(master,{},units,non_unit_owners,weapon=weapon)
    services=SimpleNamespace(owner=base.owner,need_owner_on_map=lambda checkable:True,
        rows=lambda checkable,active:rows,candidate=base.candidate,matches=base.matches,
        return_objects=base.return_objects)
    checkable=dict(owner_unit_id=owner_id,runtime_trait_id=None)
    return TraitConditionChecker(services).active_conditions(checkable,context,participation)
