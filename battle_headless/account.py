"""Import a read-only snapshot of the current sortie formation into the test runner.

Account configuration is preserved. The runner still uses the documented limited
combat profile; importing a supporter, part or skill does not execute its effects.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import struct

from battle_paths import OUTPUTS
from .engine import BattleEnv
from .stage_smoke import build_env, FixtureUnits, ControlledRules
from .random_original import OriginalRandom
from .combat_primitives import initial_mp

DEFAULT_SNAPSHOT = OUTPUTS/'battle_headless/account/current_formation_raw.json'


def xorshift(index, seed):
    value = (index*0x9e3779b1 + seed*0x85ebca77) & 0xffffffff
    value = (value ^ (value << 13)) & 0xffffffff
    value ^= value >> 17
    return (value ^ (value << 5)) & 0xffffffff


def guarded(field, bits=32):
    """Decode captured GuardTypes values and verify their redundant copy.

    Layout and stream are from this client's IL2CPP metadata/Flipper native code.
    Never call a game getter: it can update the verifier in live memory.
    """
    if bits not in (8,16,32,64):
        raise ValueError('Unsupported GuardTypes layout')
    blob = bytes.fromhex(field['bytes'])
    if not blob[0]:
        return 0
    def decode(offset, seed_offset):
        seed = struct.unpack_from('<I', blob, seed_offset)[0]
        plain = bytes(blob[offset+i] ^ ((xorshift(i//4, seed) >> (8*(i%4))) & 255)
                      for i in range(bits//8))
        return int.from_bytes(plain, 'little', signed=True)
    outer = decode({8:20,16:20,32:24,64:32}[bits], 4)
    inner = decode({8:17,16:18,32:20,64:24}[bits], 12)
    if not blob[8] or inner != outer:
        raise ValueError('GuardTypes verifier mismatch: stale or inconsistent snapshot')
    return outer


def raw(field, size=8, signed=True):
    return int.from_bytes(bytes.fromhex(field['bytes'])[:size], 'little', signed=signed)


def truncdiv(value, divisor):
    return (abs(value)//divisor) * (-1 if value<0 else 1)


def unit_stats(status):
    base = {k[1:k.index('>')]: guarded(v)
            for k,v in status['_unitStatus']['fields'].items()}
    growth = guarded(status['_growthRate'])
    grade = guarded(status['_gradeStatusCorrection'])
    if raw(status['_sspAddStatus']) or raw(status['_ultAddStatus']):
        raise NotImplementedError('SSP/ULT additive status requires its dedicated importer')
    result = {}
    for name in ('Hp','En','Attack','Defense','Mobility'):
        value = base[name] + truncdiv((base['Max'+name]-base[name])*growth,10000)
        value += truncdiv(value*grade,100)
        result[name.lower()] = max(1 if name in ('Hp','En') else 0,
                                  min(99999999 if name=='Hp' else 9999999,value))
    result['movement'] = max(0,min(9999,base['Movement']+guarded(status['_addCustomCoreMovingForce'])))
    return result


def terrain_sources(unit,master,part_id):
    from .option_parts_terrain import parts_changes
    from .terrain_capability import from_values
    captured=unit['status']['<TerrainCapability>k__BackingField']['fields']
    names=('Space','Atmospheric','Ground','Surface','Underwater')
    values={kind:guarded(captured[name]) for kind,name in enumerate(names,2)}
    preference=from_values(values)['sorted_standby_terrains']
    cached=captured.get('_sortedStandbyTerrains')
    if cached is not None and raw(cached):
        if raw(cached['fields']['_size'],4)!=len(preference):
            raise ValueError('Captured terrain capability preference count differs from original values')
    parts=unit['entity']['UserOptionPartsList']['fields']['_values']['fields']
    count=raw(parts['_size'],4)
    if count not in (0,1):raise NotImplementedError('Import each equipped part instance before resolving multiple parts')
    if count!=int(bool(part_id)):raise ValueError('Captured equipped-parts count and selected part ID disagree')
    changes=parts_changes(master,[part_id] if part_id else [])
    status_list=unit['status']['_optionPartsTerrainCapabilityChangeInfoList']
    if count==0:
        if raw(status_list):raise ValueError('Unexpected parts terrain status list without equipped parts')
    else:
        if not raw(status_list):raise ValueError('Equipped parts are missing the captured terrain change list')
        if raw(status_list['fields']['_size'],4)!=len(changes):
            raise ValueError('Captured parts terrain change count differs from original master traits')
    return dict(base_values=values,option_parts_changes=changes,
        source='captured-guarded-status-and-single-equipped-part-master-traits')


def pilot_stats(pilot):
    if pilot is None:
        raise NotImplementedError('An empty pilot slot needs the original no-pilot profile')
    status = pilot['status']
    base = status['_characterStatus']['fields']
    growth = status['_characterStatusLevelUp']['fields']
    return {k:base[k]['fields']['CurrentValue']['i32']+growth[k]['fields']['CurrentValue']['i32']
            for k in ('Ranged','Melee','Defense','Reaction','Awaken')}


def weapons(unit, master):
    result, pending = [], []
    for capture in unit['weapons']:
        live = capture['Weapon']['fields']
        weapon_id = raw(live['_mWeapon']['fields']['<Id>k__BackingField'])
        spec = master.index['m_weapon'][weapon_id]
        status = master.index['m_weapon_status'][spec['WeaponStatusId']]
        level = guarded(capture['<WeaponLevel>k__BackingField'])
        pattern = master.index['m_weapon_level_growth_pattern_set'][status['WeaponLevelGrowthPatternSetId']]
        pattern_id = status['OverrideWeaponStatusChangePatternSetId'] or pattern['WeaponStatusChangePatternSetId']
        changes = [r for r in master.tables['m_weapon_status_change_pattern']
                   if r['WeaponStatusChangePatternSetId']==pattern_id and r['CurrentWeaponLevel']==level]
        if len(changes)!=1:
            raise ValueError(f'Missing weapon level pattern: {weapon_id}/{level}')
        change = changes[0]
        attrs = [r['AttackAttributeTypeIndex'] for r in master.tables['m_attack_attribute_set']
                 if r['AttackAttributeSetId']==spec['AttackAttributeSetId']]
        cap = master.index['m_weapon_capability_set'][spec['WeaponCapabilitySetId']]
        reason = ('MAP weapon' if spec['MapWeaponRangeTypeIndex'] or spec['WeaponTypeIndex']==3
                  else 'missing attack attributes' if not attrs
                  else 'unavailable in space' if not cap['HasCapabilityInSpace'] else None)
        if reason:
            pending.append(dict(id=weapon_id,level=level,reason=reason)); continue
        ranges = live['_range']['fields']
        trait_pattern = status['OverrideWeaponTraitChangePatternSetId'] or pattern['WeaponTraitChangePatternSetId']
        trait_rows = [deepcopy(master.index['m_weapon_trait'][row['WeaponTraitId']])
                       for row in master.tables['m_weapon_trait_change_pattern']
                       if row['WeaponTraitChangePatternSetId']==trait_pattern and row['CurrentWeaponLevel']==level
                       and row['WeaponTraitId']]
        result.append(dict(id=weapon_id,name=live['Name']['text'],level=level,
            weapon_trait_types=[r['WeaponTraitTypeIndex'] for r in trait_rows],weapon_traits=trait_rows,
            power=truncdiv(guarded(live['_power'])*change['PowerCorrectionRate'],100),
            en=truncdiv(guarded(live['_enConsumption'])*change['EnCorrectionRate'],100),
            hit=truncdiv(guarded(live['_hitRate'])*change['HitRateCorrectionRate'],100),
            critical=truncdiv(guarded(live['_criticalRate'])*change['CriticalRateCorrectionRate'],100),
            base_mp_consumption=change['MpConsumptionValue'],
            base_hp_consumption_rate=change['HpConsumptionRate'],
            range_min=guarded(ranges['MinRange']),range_max=guarded(ranges['MaxRange']),
            attack_attributes=attrs,attack_attribute=attrs[0],efficiency=cap['DamageEfficiencyInSpace']/100,
            weapon_attributes=[r['WeaponAttributeTypeIndex'] for r in master.tables['m_weapon_attribute_set']
                               if r['WeaponAttributeSetId']==spec['WeaponAttributeSetId']],
            required_tension=spec['TensionTypeIndex'],source='live MasterWeapon + original level pattern'))
    return result,pending


def import_formation(path=DEFAULT_SNAPSHOT, master=None):
    from .engine import MasterData
    master = master or MasterData()
    payload = Path(path).read_bytes()
    capture = json.loads(payload)
    languages = json.loads((master.path.parent/'languages.json').read_text(encoding='utf-8'))
    def name(table, identifier):
        row=master.index[table][identifier]
        return languages[table].get(str(row['NameLanguageId']),str(identifier))
    forces=[]
    for force in capture['forces']:
        slots=[]
        for captured in force['slots']:
            unit=captured['unit']
            if unit is None:continue
            source=unit['raw']; pilot=captured['pilot']
            uid=raw(source['<Id>k__BackingField'])
            if guarded(unit['entity']['<Id>k__BackingField']['fields']['_id'],64)!=uid:
                raise ValueError('Formation unit instance does not match its user record')
            unit_id=raw(source['<UnitId>k__BackingField'])
            character_id=raw(pilot['master']['<Id>k__BackingField'])
            character_exp=guarded(pilot['entity']['<TotalExp>k__BackingField'],64)
            character_level=max(r['Level'] for r in master.tables['m_character_level_info'] if r['Exp']<=character_exp)
            available,pending=weapons(unit,master)
            if not available:raise ValueError(f'No supported weapons: {unit_id}')
            exp=raw(source['<Exp>k__BackingField'],4)
            level=max(r['Level'] for r in master.tables['m_unit_level'] if r['UnitLevelSetId']==1 and r['Exp']<=exp)
            position=guarded(captured['slot']['PositionNumber'])
            stats=unit_stats(unit['status'])
            part_id=raw(source['<OptionPartsId>k__BackingField'])
            terrain=terrain_sources(unit,master,part_id)
            skill_rows=[r for r in master.tables['m_character_skill_set'] if r['CharacterId']==character_id]
            slots.append(dict(id=f'account_{force["dictionary_key"]+1}_{position}',user_unit_id=str(uid),
                unit_id=unit_id,name=name('m_unit',unit_id),force=force['dictionary_key']+1,slot=position,
                level=level,exp=exp,limit_break=raw(source['<LimitBreakStep>k__BackingField'],2),
                option_parts_id=part_id,terrain_capability_sources=terrain,
                character_id=character_id,character_name=name('m_character',character_id),
                character_level=character_level,character_exp=character_exp,
                character_specialized=bool(guarded(pilot['entity']['IsSpecialized'],8)),
                pilot=pilot_stats(pilot),stats=stats,weapons=available,pending_weapons=pending,
                character_skill_configuration=skill_rows,
                character_ability_configuration=[r for r in master.tables['m_character_ability_set']
                                                  if r['CharacterId']==character_id],
                unit_ability_configuration=[r for r in master.tables['m_unit_ability_set'] if r['UnitId']==unit_id]))
        supporters=[]
        for support in force['supporters']:
            master_support=support['<MasterSupporter>k__BackingField']['fields']
            exp=guarded(support['<TotalExp>k__BackingField'],64)
            supporters.append(dict(id=guarded(master_support['<Id>k__BackingField']['fields']['_id'],64),
                user_supporter_id=str(guarded(support['<Id>k__BackingField']['fields']['_id'],64)),
                exp=exp,level=max(r['Level'] for r in master.tables['m_supporter_level'] if r['Exp']<=exp),
                limit_break=guarded(support['<LimitBreakStep>k__BackingField']['fields']['<Value>k__BackingField'],16),
                name=master_support['<Name>k__BackingField']['text']))
        from .supporters import compile_supporter
        for supporter in supporters:
            supporter['original_definition']=compile_supporter(master,supporter)
        forces.append(dict(number=force['dictionary_key']+1,slots=slots,supporters=supporters))
    return dict(schema='account-formation-v1',captured_at=capture['captured_at'],
        source=capture['source'],snapshot_sha256=hashlib.sha256(payload).hexdigest(),master_sha256=master.sha256,
        source_content_target_id=guarded(capture['selection']['ContentTargetId'],64),
        source_formation_type=guarded(capture['selection']['GroupFormationType']),forces=forces,
        limitations=['Explicit owner passives and supported scalar supporter effects execute; unsupported conditions and skills remain pending',
            'Unit stats include growth and grade; unsupported traits retain explicit pending reasons',
            'MAP weapons and weapons unusable in space are retained as pending entries',
            'Initial MP uses original 0..12 clamping and the supported initial-MP owner traits',
            'Original player formation coordinates and sortie terrain are imported; NPC deployment and complete global initialization remain pending',
            'Standard phase cycle and ordinary chance steps execute; the three-turn horizon is not original stage outcome scheduling'])


def build_account_env(formation, stage=None, seed=37, owner_passives=True,*,trait_source_time_ms=None):
    baseline,stage=build_env(stage,seed)
    if formation['master_sha256']!=stage.master.sha256:raise ValueError('Master data mismatch')
    units={k:deepcopy(v) for k,v in baseline.units.items() if v['team']=='enemy'}
    anchors=stage.source['m_map_stage_group_initial_placement']
    from .deployment import formation_positions
    from .occupancy import UnitFootprint
    from .map_terrain import MapTerrainRuntime
    from .terrain_capability import upgrade
    from .sortie import prepare_sortie_changes,sortie_status
    from .trait_sources import capture_time_ms
    # Deployment precedes activation of the initial global trait repository.
    # Terrain capability therefore comes from captured user status + parts.
    # Only the resolved chip dictionary is needed here, not movement geometry.
    chips=MapTerrainRuntime.from_stage(stage,None).chips
    sortie_records=[]
    source_time=(capture_time_ms(formation.get('captured_at')) if trait_source_time_ms is None
                 else int(trait_source_time_ms))
    time_basis='snapshot_capture' if trait_source_time_ms is None else 'explicit'
    for force in formation['forces']:
        anchor=next(a for a in anchors if a['BattleSidePlacedTypeIndex']==force['number'])
        # Current normalized capture is a complete five-slot formation. The
        # native converter assigns increasing UnitStepOrder in this sequence;
        # Deploy sorts by it, then places list entries 0..4 independently.
        # Empty/truncated captures need the converter's original null slots.
        if [slot['slot'] for slot in force['slots']]!=[1,2,3,4,5]:
            raise NotImplementedError('Deployment requires the original complete five-slot capture order')
        if stage.stage['Group'+str(force['number'])+'SortieCount']!=5:
            raise NotImplementedError('Original sortie-count selection for this formation is required')
        footprints=[UnitFootprint.from_row(stage.master.index['m_occupied_area'][
            stage.master.index['m_unit'][slot['unit_id']]['OccupiedAreaId']]) for slot in force['slots']]
        direction=anchor['DirectionTypeIndex']
        positions=formation_positions((anchor['X'],anchor['Y']),direction,footprints)
        for slot,position,footprint in zip(force['slots'],positions,footprints):
            stat=slot['stats']
            units[slot['id']]=dict(id=slot['id'],unit_id=slot['unit_id'],team='player',position=list(position),
                direction=direction,**stat,max_hp=stat['hp'],max_en=stat['en'],pilot=slot['pilot'],mp=initial_mp(),
                weapons=deepcopy(slot['weapons']),moved=False,acted=False,account_configuration=deepcopy(slot))
            units[slot['id']]['account_configuration'].update(trait_source_time_ms=source_time,
                trait_source_time_basis=time_basis)
            if 'terrain_capability_sources' in slot:
                units[slot['id']]['terrain_capability_sources']=deepcopy(slot['terrain_capability_sources'])
            else:raise NotImplementedError('Original captured terrain capability required for sortie')
            terrain=slot['terrain_capability_sources']
            capability=upgrade(terrain['base_values'],terrain['option_parts_changes'])
            changes=prepare_sortie_changes(is_npc=False,stage_type=stage.stage['StageTerrainTypeIndex'],
                user_capability=capability,current_standby_type=0,standby_capability=capability,
                chips=chips,coords=footprint.ordered_coords(position,direction))
            if ('existence',9) in changes:
                raise NotImplementedError(f"Original CannotSortie state for {slot['id']} requires the full existence lifecycle; writes={changes}")
            units[slot['id']].update(changes)
            sortie_records.append(dict(unit_id=slot['id'],position=list(position),
                status=sortie_status(capability,stage.stage['StageTerrainTypeIndex']),writes=changes))
    scenario=dict(width=stage.width,height=stage.height,max_turns=3,blocked=[],
        phase_clock='original-standard',phase_teams={'0':'player','2':'enemy'},
        units=[dict(id=u['id'],unit_id=u['unit_id'],team=u['team'],position=u['position'])
               for u in sorted(units.values(),key=lambda u:u['team']!='player')])
    env=BattleEnv(FixtureUnits(stage,units),ControlledRules(stage.master))
    env.reset(scenario,seed);env.rng=OriginalRandom(seed)
    env.deployment_profile=dict(coordinates='original-five-slot-formation',
        source_methods=['0x6222c58','0x62247d8','0x61546ec','0x613ec80','0x5f33fa8','0x6180ef8'],
        player_sortie_terrain='original-before-global-trait-activation',player_sorties=sortie_records,
        prepare_sortie_complete=False)
    env.supporter_sources=[dict(id=s['id'],definition=deepcopy(s['original_definition']),
        group=dict(link_type=2,side=0,id='account:'+str(force['number'])))
        for force in formation['forces'] for s in force['supporters']]
    env.passive_summary=[]
    if owner_passives:
        from .owner_passives import attach_owner_passives
        env.passive_summary=attach_owner_passives(env,stage)
    from .turns import attach_original_actions
    attach_original_actions(env,stage)
    return env,stage


def main():
    import argparse
    from .__main__ import choose
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot',type=Path,default=DEFAULT_SNAPSHOT)
    parser.add_argument('--out',type=Path,default=OUTPUTS/'battle_headless/account')
    parser.add_argument('--owner-passives',action=argparse.BooleanOptionalAction,default=True,
                        help='Apply the explicit supported owner-only passive subset (default enabled)')
    parser.add_argument('--strict-original',action='store_true',help='Refuse execution until the complete original battle contract passes')
    args=parser.parse_args()
    formation=import_formation(args.snapshot)
    env,stage=build_account_env(formation,owner_passives=args.owner_passives)
    passive_summary=env.passive_summary
    args.out.mkdir(parents=True,exist_ok=True)
    from .fidelity import report as fidelity_report,require_complete_original
    fidelity=fidelity_report(env,formation)
    (args.out/'fidelity.json').write_text(json.dumps(fidelity,ensure_ascii=False,indent=2),encoding='utf-8')
    if args.strict_original:require_complete_original(env,formation)
    (args.out/'formation.json').write_text(json.dumps(formation,ensure_ascii=False,indent=2),encoding='utf-8')
    lines=['# 当前游戏配队导入','',
        f"来源关卡：{formation['source_content_target_id']}；测试关卡：{stage.stage_id} Hard。",'',
        f"快照时间（UTC）：{formation['captured_at']}",'']
    for force in formation['forces']:
        lines += [f"## 部队 {force['number']}",'',
            '| 槽位 | 机体 | 等级 / 突破 | 驾驶员 | 等级 | OP |',
            '|---|---|---|---|---|---|']
        for slot in force['slots']:
            lines.append(f"| {slot['slot']} | {slot['name']} | {slot['level']} / {slot['limit_break']} | "
                         f"{slot['character_name']} | {slot['character_level']} | {slot['option_parts_id']} |")
        lines += ['', '支援：'+'；'.join(f"{s['name']} Lv{s['level']} / 突破{s['limit_break']}"
                                       for s in force['supporters']),'']
    lines += ['## 当前运行限制','']+['- '+item for item in formation['limitations']]+['']
    (args.out/'formation.md').write_text('\n'.join(lines),encoding='utf-8')
    initial=env.observe()
    while not (env.terminated or env.truncated):env.step(choose(env))
    report=env.replay()
    report.update(stage_id=stage.stage_id,formation=formation,initial=initial,exact_gameplay=False,passive_summary=passive_summary,
        limitations=formation['limitations']+['Counters, support, skills, full conditional traits and stage event scheduling remain pending'])
    (args.out/'smoke_replay.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    summary=dict(forces=len(formation['forces']),players=sum(len(f['slots']) for f in formation['forces']),
        stage=stage.stage_id,actions=len(env.events),winner=env.winner,exact_gameplay=False)
    (args.out/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    print(json.dumps(summary))


if __name__=='__main__':main()
