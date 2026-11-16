"""Read original Eternal Road stages and preserve all source parameters."""
from copy import deepcopy
import json
from pathlib import Path
import re

from battle_paths import MASTER_SNAPSHOT, RESOURCE_CACHE
from .engine import MasterData

DEFAULT_STAGE = 90510005


class StageFixture:
    def __init__(self, master=None, stage_id=DEFAULT_STAGE):
        self.master = master or MasterData()
        self.tables = self.master.tables
        self.stage_id = stage_id
        self.stage = self.one('m_stage', Id=stage_id)
        self.road = self.one('m_eternal_road_stage', StageId=stage_id)
        if self.road['StageDifficultyTypeIndex'] != 2:
            raise ValueError('This fixture requires an Eternal Road HARD stage')
        # Map-stage difficulty is a separate index, NOT the Eternal Road difficulty.
        self.map_stage = self.one('m_map_stage', StageId=stage_id)
        self.map = self.one('m_map', Id=self.map_stage['MapId'])
        self.width, self.height = self.map['Width'], self.map['Height']
        self.source = {}
        for name in self.tables:
            if name.startswith('m_map_'):
                rows = [r for r in self.tables[name] if r.get('MapStageId')==self.map_stage['Id']]
                if rows:
                    self.source[name] = deepcopy(rows)
        self.motifs = self.select('m_map_chip_motif', MapId=self.map['Id'])
        self.terrain, self.extra_motif_coords = {}, []
        for motif in self.motifs:
            terrain_ids = [r['TerrainMotifId'] for r in self.select('m_map_chip_motif_terrain_motif', MapChipMotifId=motif['MapChipMotifId'])]
            for x,y in re.findall(r'\((-?\d+),\s*(-?\d+)\)',motif['CoordinateList']):
                pos = (int(x),int(y))
                entry = [self.one('m_terrain_motif',Id=i) for i in terrain_ids]
                # Preserve overscan authoring coordinates, don't silently resize the map.
                if not self.in_bounds(pos):
                    self.extra_motif_coords.append(dict(position=list(pos),terrain=deepcopy(entry)))
                else:
                    self.terrain.setdefault(pos,[]).extend(deepcopy(entry))
        self.npcs = []
        for npc in self.source.get('m_map_npc',[]):
            key = npc['Id']
            units = self.select('m_map_npc_unit',MapNpcId=key)
            characters = self.select('m_map_npc_character',MapNpcId=key)
            versions = []
            for unit in units:
                row = deepcopy(unit)
                row['weapons'] = self.select('m_map_npc_unit_weapon_set_content',
                                            MapNpcUnitWeaponSetId=unit['MapNpcUnitWeaponSetId'])
                versions.append(row)
            self.npcs.append(dict(placement=deepcopy(npc),units=versions,characters=characters,
                ai=self.select('m_map_npc_ai',MapNpcId=key),
                setting=self.select('m_map_npc_setting',MapNpcId=key),
                terrain_extensions=self.select('m_map_npc_unit_terrain_capability_extension',MapNpcId=key)))
        self.name = str(stage_id)
        languages = MASTER_SNAPSHOT/'languages.json'
        if languages.exists():
            labels = json.loads(languages.read_text(encoding='utf-8'))
            self.name = labels.get('m_eternal_road_stage',{}).get(str(self.road['StageNameLanguageId']),self.name)
        self.conditions = self.source.get('m_map_stage_condition',[])

    def select(self, table, **keys):
        return deepcopy([r for r in self.tables.get(table,[]) if all(r.get(k)==v for k,v in keys.items())])

    def one(self, table, **keys):
        rows = self.select(table,**keys)
        if len(rows)!=1:
            raise ValueError(f'Expected one {table} {keys}; got {len(rows)}')
        return rows[0]

    def in_bounds(self, position):
        return 0 <= position[0] < self.width and 0 <= position[1] < self.height

    def evaluate_conditions(self, alive_by_side, turn_count):
        """Evaluate predicates with supplied elapsed turns (native Number - 1)."""
        passed = []
        for row in self.conditions:
            kind = row['IngameEventConditionTypeIndex']
            if kind==1 and row['IngameEventConditionParameterTypeAIndex']==5:
                ok = alive_by_side.get(int(row['ParameterValueA']),0)==0
            elif kind==7:
                # TurnCountCondition.Evaluate 0x5f00010: target <= turnCount.
                ok = turn_count >= row['TargetValue']
            else:
                raise NotImplementedError(f'Condition {kind} requires original implementation')
            if ok:
                passed.append(dict(id=row['Id'],condition_type=row['MapStageConditionTypeIndex']))
        return passed

    def package(self):
        # Preserve the source tables needed to resolve all NPC abilities/traits.
        # Runtime trait execution is separate from extraction of these definitions.
        ability_ids = set()
        unit_set_ids = {u['MapNpcUnitAbilitySetId'] for npc in self.npcs for u in npc['units']} - {0}
        char_set_ids = {c['MapNpcCharacterAbilitySetId'] for npc in self.npcs for c in npc['characters']} - {0}
        dependencies = {}
        unit_ids={u['UnitId'] for npc in self.npcs for u in npc['units']}
        character_ids={c['CharacterId'] for npc in self.npcs for c in npc['characters']}
        weapon_ids={w['WeaponId'] for npc in self.npcs for u in npc['units'] for w in u['weapons']}
        for table,field,ids in [('m_unit','Id',unit_ids),('m_character','Id',character_ids),
                ('m_weapon','Id',weapon_ids),('m_unit_ability_set','UnitId',unit_ids),
                ('m_character_ability_set','CharacterId',character_ids)]:
            dependencies[table]=[r for r in self.tables[table] if r[field] in ids]
        ability_ids.update(r['AbilityId'] for table in ['m_unit_ability_set','m_character_ability_set']
                           for r in dependencies[table])
        for table,field,ids in [('m_map_npc_unit_ability_set_content','MapNpcUnitAbilitySetId',unit_set_ids),
                ('m_map_npc_character_ability_set_content','MapNpcCharacterAbilitySetId',char_set_ids)]:
            dependencies[table] = [r for r in self.tables[table] if r[field] in ids]
            ability_ids.update(r['AbilityId'] for r in dependencies[table])
        dependencies['m_ability'] = [r for r in self.tables['m_ability'] if r['Id'] in ability_ids]
        trait_sets = {r['TraitSetId'] for r in dependencies['m_ability']}
        dependencies['m_trait_set'] = [r for r in self.tables['m_trait_set'] if r['Id'] in trait_sets]
        trait_ids = {r['TraitId'] for r in dependencies['m_trait_set']}
        dependencies['m_trait'] = [r for r in self.tables['m_trait'] if r['Id'] in trait_ids]
        condition_sets = {r.get(field,0) for r in dependencies['m_trait']
                          for field in ['ActiveConditionSetId','TargetConditionSetId','ValueSourceConditionSetId','TraitBoostConditionSetId']} - {0}
        dependencies['m_trait_condition'] = [r for r in self.tables['m_trait_condition'] if r['TraitConditionSetId'] in condition_sets]
        dependencies['m_trait_set_detail'] = [r for r in self.tables['m_trait_set_detail'] if r['TraitSetId'] in trait_sets]
        weapon_trait_sets = {w['MapNpcWeaponTraitSetId'] for npc in self.npcs for u in npc['units'] for w in u['weapons']} - {0}
        dependencies['m_map_npc_unit_weapon_trait_set_content'] = [r for r in self.tables['m_map_npc_unit_weapon_trait_set_content'] if r['MapNpcWeaponTraitSetId'] in weapon_trait_sets]
        wtraits = {r['WeaponTraitId'] for r in dependencies['m_map_npc_unit_weapon_trait_set_content']}
        dependencies['m_weapon_trait'] = [r for r in self.tables['m_weapon_trait'] if r['Id'] in wtraits]
        for table,field,ids in [
                ('m_weapon_status','Id',{r['WeaponStatusId'] for r in dependencies['m_weapon']}),
                ('m_attack_attribute_set','AttackAttributeSetId',{r['AttackAttributeSetId'] for r in dependencies['m_weapon']}),
                ('m_weapon_capability_set','Id',{r['WeaponCapabilitySetId'] for r in dependencies['m_weapon']}),
                ('m_occupied_area','Id',{r['OccupiedAreaId'] for r in dependencies['m_unit']})]:
            dependencies[table]=[r for r in self.tables[table] if r[field] in ids]
        resource_name='stage_'+self.stage['ResourceId']
        index_path=MASTER_SNAPSHOT/'all_resource_index.json'
        resource_index=json.loads(index_path.read_text(encoding='utf-8'))
        bundle=resource_index.get(resource_name)
        bundle_evidence=dict(resource_name=resource_name,index=bundle,cached=False)
        audit=json.loads((RESOURCE_CACHE/'latest_game_audit.json').read_text(encoding='utf-8'))
        if bundle:
            relative=Path('内部数据/com.bandainamcoent.gget_WW/files/octo/v1/2001')/str(bundle['id']%10)/('A'+str(bundle['id'])).encode().hex()/bundle['md5']
            bundle_evidence['cached']=any((Path(audit[key])/relative).is_file() for key in ['raw_export','baseline_export'])
        return deepcopy(dict(schema=1,stage_id=self.stage_id,name=self.name,difficulty='Hard',
            master_sha256=self.master.sha256,stage=self.stage,eternal_road=self.road,map_stage=self.map_stage,
            map=self.map,source=self.source,motifs=self.motifs,extra_motif_coords=self.extra_motif_coords,
            npcs=self.npcs,dependencies=dependencies,stage_bundle=bundle_evidence,
            limitations=['No player account formation supplied',
                         'Overscan motif coordinates preserved separately from declared dimensions',
                         'Native battle methods are compiled code; MonoScript references are not C# source',
                         'Stage-specific bundle is absent from both local backups; scripted events are not verified']))
