"""Stage choices from the master data used by the local battle runtime."""

from dataclasses import dataclass
import json
from pathlib import Path
from tempfile import NamedTemporaryFile

from prepare_android import ARTIFACTS
from battle_paths import MASTER_SNAPSHOT


SOURCE = MASTER_SNAPSHOT
MASTER_TABLES = SOURCE / 'parsed_tables.json'
LANGUAGES = SOURCE / 'languages.json'
STAGE_UPDATE = ARTIFACTS / 'stage_update'
UPDATED_TABLES = STAGE_UPDATE / 'stage_tables.json'
UPDATED_LANGUAGES = STAGE_UPDATE / 'stage_languages.json'
TOWER, INVASION, ROAD_EX, ROAD_HARD = '永恒之塔', '强敌来袭', '永恒之路 EX', '永恒之路 Hard'
STORY_EXPANSION = '剧情关卡'
ROAD_DIFFICULTIES = {2: ROAD_HARD, 3: ROAD_EX}
INVASION_STAGE_TYPE, ROAD_STAGE_TYPE, TOWER_STAGE_TYPE = 7, 8, 9
STAGE_NAMES = {90510001: '宇宙世纪系列', 90510005: '机动战士高达',
               90510006: '机动战士Z高达', 90510033: '机动战士高达AGE'}
# Original resource IDs for the stages exercised with the local deployment.
EXPANSION_STAGES = {
    2200204: '0220_02_04',
    6010203: '0601_02_03',
    6010303: '0601_03_03',
    6010402: '0601_04_02',
}


@dataclass(frozen=True)
class StageChoice:
    label: str
    stage_id: int
    invasion_level: int = 0


def tower_floors(tables):
    # The 15-floor events and reruns share their battle StageIds with the expanded towers.
    return {row['StageId']: row for row in sorted(tables['m_tower_event_stage'], key=lambda row: row['Id'])}


def build_catalog(tables, names):
    groups = {row['Id']: row for row in tables['m_tower_event_stage_group']}
    tower = {}
    for row in sorted(tower_floors(tables).values(), key=lambda row: row['StageId']):
        name_id = groups[row['TowerEventStageGroupId']]['TowerNameLanguageId']
        name = names['m_tower_event_stage_group'][str(name_id)]
        label = f"{row['FloorCount']:02d}F"
        tower.setdefault(name, []).append([label, row['StageId']])
    for choices in tower.values():
        choices.sort()

    units = {row['Id']: row['NameLanguageId'] for row in tables['m_unit']}
    floors = {}
    for row in sorted(tables['m_invasion_event_stage_extend'], key=lambda row: row['Level']):
        floors.setdefault(row['InvasionEventStageGroupId'], []).append(row['Level'])
    invasion = {}
    for group in sorted(tables['m_invasion_event_stage_group'], key=lambda row: row['Priority']):
        name = names['m_unit'][str(units[group['UnitId']])]
        invasion[name] = [[f'第 {level:02d} 层', group['ExtendStageId'], level] for level in floors[group['Id']]]

    road = {category: [] for category in ROAD_DIFFICULTIES.values()}
    for row in sorted(tables['m_eternal_road_stage'], key=lambda row: (row['StageNumber'], row['StageId'])):
        if category := ROAD_DIFFICULTIES.get(row['StageDifficultyTypeIndex']):
            name = names['m_eternal_road_stage'][str(row['StageNameLanguageId'])]
            road[category].append([f"{row['StageNumber']:02d}  {name}", row['StageId']])
    return {TOWER: tower, INVASION: invasion, **{category: {'': rows} for category, rows in road.items()}}


def load_catalog(names):
    # Keep the 147 MB parsed table out of routine window startup.
    path = ARTIFACTS / 'stage_catalog.json'
    try:
        cache = json.loads(path.read_bytes())
    except (OSError, ValueError):
        cache = {}
    updated = UPDATED_TABLES.is_file() and UPDATED_LANGUAGES.is_file()
    tables_path, language_path = (UPDATED_TABLES, UPDATED_LANGUAGES) if updated else (MASTER_TABLES, LANGUAGES)
    try:
        revision = [4, tables_path.name, tables_path.stat().st_mtime_ns, language_path.stat().st_mtime_ns]
    except FileNotFoundError:
        if 'catalog' not in cache:
            raise
        revision = None
    if revision is None or cache.get('revision') == revision:
        catalog = cache['catalog']
    else:
        catalog = build_catalog(json.loads(tables_path.read_bytes()),
                                json.loads(language_path.read_bytes()) if updated else names)
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(dir=path.parent, suffix='.tmp', delete=False) as pending:
            temporary = Path(pending.name)
        try:
            temporary.write_text(json.dumps({'revision': revision, 'catalog': catalog}, ensure_ascii=False), 'utf-8')
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    return {category: {name: [StageChoice(*row) for row in rows] for name, rows in groups.items()}
            for category, groups in catalog.items()}
