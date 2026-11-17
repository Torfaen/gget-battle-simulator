"""Build local sortie formations using the original master and user tables."""

from base64 import b64decode, b64encode
from bisect import bisect_right
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from functools import lru_cache
import json
from pathlib import Path
import re
from tempfile import NamedTemporaryFile

from prepare_stage import ARTIFACTS, BASE_STAGE_DIR, STAGE, UPDATED_MASTER, pack_memory_database, stage_directory
from battle_paths import OUTPUTS
from stage_catalog import INVASION_STAGE_TYPE, UPDATED_LANGUAGES
from formation_rules import (TERRAIN_CHANGE_TYPES, RatingTrait, additional_parameter, aggregate, change_record,
                             character_stat, leader_matches, leader_uses_or, round_up_double, sortie_status,
                             stat_point, truncdiv, upgrade)

import msgpack


BASE_MASTER = BASE_STAGE_DIR / 'master_database.bytes'
TEAM_ENTRY = 'Formation.json'
LAST_FORMATION = ARTIFACTS / 'last_formation.ggetteam'
TEAM_SAVES = ARTIFACTS / 'formation_saves'
MAX_TEAM_BYTES = 65536
MAX_TEAM_PREVIEW = 128 * 1024
TERRAIN_NAMES = ('通用', '宇宙', '大气层', '地面', '水域', '地面／水域')
TERRAIN_SHORT = ('宇', '空', '地', '水', '潜')
CAPABILITY_GRADE = {1: '×', 2: '△', 3: '○'}
ROLES = {1: '攻击型', 2: '防御型', 3: '支援型'}
UNIT_RARITIES = {1: 'N', 2: 'R', 3: 'SR', 4: 'SSR', 5: 'UR'}


@dataclass(frozen=True)
class ProfileEntry:
    name: str
    icon: str
    level: int
    effects: tuple
    costs: tuple = ()


@dataclass(frozen=True)
class SupporterInfo:
    active: ProfileEntry | None
    leaders: tuple
    use_max: bool
    hp: int
    attack: int


@dataclass
class Member:
    unit: int
    pilot: int
    level: int
    pilot_level: int
    stars: int
    sp: bool
    ssp: bool
    ssp_level: int
    op: int
    weapons: dict
    ssp_cores: dict | None = None
    pilot_specialized: bool | None = None


@dataclass
class Force:
    members: list
    supporter: int
    supporter_level: int


def encode_team(team, *, stage=None, preview=None):
    profile = {'version': 1, 'forces': [asdict(force) for force in team]}
    for force in profile['forces']:
        for member in force['members']:
            for field in ('ssp_cores', 'pilot_specialized'):
                if member[field] is None:
                    del member[field]
    if stage is not None:
        profile.update(stage_id=stage.stage_id, invasion_level=stage.invasion_level)
    if preview:
        profile['preview_jpeg'] = b64encode(preview).decode('ascii')
    return json.dumps(profile, ensure_ascii=False, indent=2).encode('utf-8')


def decode_team(data):
    return _decode_sortie(data)[0]


def _decode_sortie(data):
    integer_fields = ('unit', 'pilot', 'level', 'pilot_level', 'stars', 'ssp_level', 'op')
    member_fields = set(integer_fields) | {'sp', 'ssp', 'weapons'}
    optional_fields = {'ssp_cores', 'pilot_specialized'}
    try:
        profile = json.loads(data)
        forces = profile['forces']
        if profile['version'] != 1 or not isinstance(forces, list) or len(forces) != 2:
            raise ValueError
        team = []
        for force in forces:
            if set(force) != {'members', 'supporter', 'supporter_level'}:
                raise ValueError
            if any(type(force[key]) is not int for key in ('supporter', 'supporter_level')):
                raise ValueError
            if not isinstance(force['members'], list) or len(force['members']) != 5:
                raise ValueError
            members = []
            for member in force['members']:
                if (set(member) - optional_fields != member_fields or
                        any(type(member[key]) is not int for key in integer_fields)):
                    raise ValueError
                if type(member['sp']) is not bool or type(member['ssp']) is not bool:
                    raise ValueError
                weapons = member['weapons']
                if len(weapons) > 64 or any(not key.isascii() or not key.isdecimal() or len(key) > 18
                                            or type(level) is not int for key, level in weapons.items()):
                    raise ValueError
                if 'pilot_specialized' in member and type(member['pilot_specialized']) is not bool:
                    raise ValueError
                if 'ssp_cores' in member:
                    cores = member['ssp_cores']
                    if (not isinstance(cores, dict) or len(cores) > 64 or
                            any(not key.isascii() or not key.isdecimal() or len(key) > 18 or
                                type(point) is not int or point < 0 for key, point in cores.items())):
                        raise ValueError
                    member['ssp_cores'] = {int(key): point for key, point in cores.items()}
                members.append(Member(**(member | {'weapons': {int(key): level for key, level in weapons.items()}})))
            team.append(Force(members, force['supporter'], force['supporter_level']))
        stage_id, level = profile.get('stage_id'), profile.get('invasion_level')
        stage = None
        if type(stage_id) is int and stage_id > 0 and type(level) is int and level >= 0:
            stage = stage_id, level
        return team, stage
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise ValueError('队伍文件格式无效') from exc


def load_team(source):
    return load_sortie(source)[0]


def load_sortie(source):
    return _decode_sortie(read_team_data(source))


def read_team_data(source):
    limit = MAX_TEAM_BYTES + MAX_TEAM_PREVIEW * 2
    data = source.read(limit + 1)
    if len(data) > limit:
        raise ValueError('队伍文件过大')
    return data


def team_preview(path):
    with path.open('rb') as source:
        profile = json.loads(read_team_data(source))
    preview = profile.get('preview_jpeg') if isinstance(profile, dict) else None
    if isinstance(preview, str) and len(preview) <= (MAX_TEAM_PREVIEW + 2) // 3 * 4:
        return b64decode(preview, validate=True), ''
    return None, ''


def save_team(path, team, *, stage=None, preview=None):
    path = Path(path)
    with NamedTemporaryFile(dir=path.parent, suffix='.tmp', delete=False) as output:
        pending = Path(output.name)
    try:
        pending.write_bytes(encode_team(team, stage=stage, preview=preview))
        pending.replace(path)
    finally:
        pending.unlink(missing_ok=True)


def packed_tables(path, names=None):
    with path.open('rb') as source:
        unpacker = msgpack.Unpacker(source, raw=False)
        header = unpacker.unpack()
        start = unpacker.tell()
        for name, (offset, size) in header.items():
            if names is None or name in names:
                source.seek(start + offset)
                yield name, source.read(size)


def rows_by(rows, index=0):
    return {row[index]: row for row in rows}


def rows_grouped(rows):
    grouped = defaultdict(list)
    for row in rows:
        grouped[row[0]].append(row[1:])
    return grouped


def level_for(levels, exp):
    return bisect_right(list(levels.values()), exp)


@lru_cache(maxsize=1)
def formation_names():
    names = {}
    for path in (ARTIFACTS / 'battle_ui_assets/master_names.json', UPDATED_LANGUAGES,
                 ARTIFACTS / 'battle_ui_assets/formation_names.json'):
        if path.is_file():
            for table, entries in json.loads(path.read_bytes()).items():
                names.setdefault(table, {}).update(entries)
    capture = OUTPUTS / 'battle_headless/account/formation.json'
    if capture.is_file():
        supporters = names.setdefault('m_supporter', {})
        for force in json.loads(capture.read_bytes())['forces']:
            for row in force['supporters']:
                supporters.setdefault(str(160100000000000000 + row['id']), row['name'])
    return names


@lru_cache(maxsize=1)
def exported_mech_assets():
    try:
        latest = json.loads((OUTPUTS / 'latest_mech_assets.json').read_bytes())
        directory = OUTPUTS / 'updates' / latest['run_id']
        return directory if directory.is_dir() else None
    except (OSError, ValueError, KeyError):
        return None


def master_for_stage(stage_id):
    if stage_id == STAGE or not UPDATED_MASTER.is_file():
        return BASE_MASTER
    # Newly extracted stages use the updated tables. Older prepared stages may still pin the base.
    manifest = stage_directory(stage_id) / 'manifest.json'
    if manifest.is_file():
        base_manifest = json.loads((BASE_STAGE_DIR / 'manifest.json').read_bytes())
        if json.loads(manifest.read_bytes())['master_database']['sha256'] == base_manifest['master_database']['sha256']:
            return BASE_MASTER
    return UPDATED_MASTER


class FormationCatalog:
    def __init__(self, master):
        names = {
            'm_unit', 'm_character', 'm_supporter', 'm_option_parts', 'm_weapon', 'm_stage',
            'm_character_skill_set', 'm_character_skill', 'm_character_skill_trait', 'm_character_skill_trait_set',
            'm_character_ability_set', 'm_ability', 'm_trait_set_detail', 'm_schedule',
            'm_terrain_capability_set', 'm_unit_level', 'm_character_level_info', 'm_supporter_level',
            'm_unit_rarity_max_level', 'm_character_rarity_max_level', 'm_supporter_level_limit',
            'm_supporter_limit_break_step_skill', 'm_supporter_growth', 'm_supporter_leader_skill',
            'm_supporter_leader_skill_content', 'm_supporter_active_skill', 'm_character_status', 'm_config',
            'm_unit_ssp_level_info', 'm_unit_ssp_config', 'm_unit_weapon', 'm_unit_ssp_weapon',
            'm_group_formation_unit_idx', 'm_group_formation_supporter_idx', 'm_trait_set', 'm_trait',
            'm_weapon_level_up_material_set_content', 'm_stage_sortie_restriction_set_content',
            'm_stage_sortie_restriction_set_group_content', 'm_series_set', 'm_series',
            'm_unit_lineage', 'm_character_lineage', 'm_unit_limit_break_growth', 'm_mechanism', 'm_mechanism_set',
            'm_linked_character_unit', 'm_option_parts_lineage', 'm_lineage', 'm_trait_condition',
            'm_unit_ssp_initial_release_set', 'm_unit_ssp_custom_core', 'm_unit_ssp_custom_core_release_function_set_content',
            'm_unit_ssp_custom_core_weapon_change',
            'm_unit_status', 'm_unit_ability_set', 'm_unit_skill_set_content', 'm_unit_skill',
            'm_weapon_status', 'm_weapon_level_growth_pattern_set', 'm_weapon_status_change_pattern',
            'm_weapon_trait_change_pattern', 'm_weapon_trait',
        }
        tables = {name: msgpack.unpackb(payload, raw=False) for name, payload in packed_tables(master, names)}
        self.units = rows_by(tables['m_unit'])
        self.pilots = rows_by(tables['m_character'])
        self.supporters = rows_by(tables['m_supporter'])
        self.parts = rows_by(tables['m_option_parts'])
        self.weapons = rows_by(tables['m_weapon'])
        self.stages = rows_by(tables['m_stage'])
        self.terrain = rows_by(tables['m_terrain_capability_set'])
        self.unit_exp = {row[1]: row[2] for row in tables['m_unit_level'] if row[0] == 1}
        self.pilot_exp = {row[0]: row[1] for row in tables['m_character_level_info']}
        self.pilot_growth = {row[0]: row[2] for row in tables['m_character_level_info']}
        self.pilot_status = rows_by(tables['m_character_status'])
        config = dict(tables['m_config'])
        self.pilot_power_coefficient = int(config.get(153, 100))
        self.pilot_sp = int(config[163])
        self.supporter_exp = dict(tables['m_supporter_level'])
        self.ssp_exp = {row[0]: row[1] for row in tables.get('m_unit_ssp_level_info', ())}
        self.unit_limits = dict(tables['m_unit_rarity_max_level'])
        self.pilot_limits = dict(tables['m_character_rarity_max_level'])
        self.supporter_limits = dict(tables['m_supporter_level_limit'])
        self.supporter_steps = rows_grouped(tables['m_supporter_limit_break_step_skill'])
        self.supporter_growth = {(level, step): rate for level, step, rate in tables['m_supporter_growth']}
        self.leader_skills = rows_by(tables['m_supporter_leader_skill'])
        self.leader_contents = rows_grouped(tables['m_supporter_leader_skill_content'])
        self.active_support_skills = rows_by(tables['m_supporter_active_skill'])
        self.max_stars = max(row[0] for row in tables['m_unit_limit_break_growth'])
        self.ssp_config = rows_by(tables.get('m_unit_ssp_config', ()))
        self.ssp_release = rows_grouped(tables.get('m_unit_ssp_initial_release_set', ()))
        self.unit_weapons = rows_grouped(tables['m_unit_weapon'])
        self.unit_status = rows_by(tables.get('m_unit_status', ()))
        self.unit_abilities = rows_grouped(tables.get('m_unit_ability_set', ()))
        self.unit_skill_sets = rows_grouped(tables.get('m_unit_skill_set_content', ()))
        self.unit_skills = rows_by(tables.get('m_unit_skill', ()))
        self.weapon_status = rows_by(tables.get('m_weapon_status', ()))
        self.weapon_growth = rows_by(tables.get('m_weapon_level_growth_pattern_set', ()))
        self.weapon_level_one = {row[0]: row[2:] for row in tables.get('m_weapon_status_change_pattern', ())
                                 if row[1] == 1}
        self.weapon_traits_level_one = rows_grouped(
            (pattern, slot, trait) for pattern, level, slot, trait in tables.get('m_weapon_trait_change_pattern', ())
            if level == 1 and trait)
        self.weapon_traits = rows_by(tables.get('m_weapon_trait', ()))
        self.ssp_weapons = rows_grouped(tables.get('m_unit_ssp_weapon', ()))
        self.ssp_cores = rows_by(tables.get('m_unit_ssp_custom_core', ()))
        self.core_functions = rows_grouped(tables.get('m_unit_ssp_custom_core_release_function_set_content', ()))
        self.core_weapons = rows_by(tables.get('m_unit_ssp_custom_core_weapon_change', ()))
        self.group_slots = rows_grouped(sorted(
            (group, force, position, idx) for idx, group, force, position in tables['m_group_formation_unit_idx']))
        self.group_supporters = rows_grouped(sorted(
            (group, force, position, idx) for idx, group, force, position in tables['m_group_formation_supporter_idx']))
        trait_sets = rows_grouped(tables['m_trait_set'])
        traits = rows_by(tables['m_trait'])
        self.trait_conditions = rows_grouped(([row[1], row[4], row[5], row[6], row[28], row[29]]
                                              for row in tables['m_trait_condition']))
        self.leader_conditions = rows_grouped(([row[1], row[2], row[4], row[5], row[6]]
                                               for row in tables['m_trait_condition']))
        self.trait_sets = trait_sets
        self.traits = traits
        self.self_condition_sets = {row[1] for row in tables['m_trait_condition']
                                    if row[3] == 'Owner' and not any(row[4:])}
        self.trait_details = rows_by(tables['m_trait_set_detail'])
        self.pilot_skills = rows_grouped(tables['m_character_skill_set'])
        self.pilot_abilities = rows_grouped(tables['m_character_ability_set'])
        self.skill_info = rows_by(tables['m_character_skill'])
        self.skill_trait_sets = rows_grouped(tables['m_character_skill_trait_set'])
        self.skill_traits = rows_by(tables['m_character_skill_trait'])
        self.ability_info = rows_by(tables['m_ability'])
        self.schedules = rows_by(tables['m_schedule'])
        self.part_traits = {}
        self.part_terrain = {}
        for part, row in self.parts.items():
            self.part_traits[part] = [(traits[trait][3], tuple(
                int(tag) for tags, *_ in self.trait_conditions.get(traits[trait][6], ())
                for tag in tags.replace(',', ' ').split() if tag.isdecimal()))
                for (trait,) in trait_sets[row[3]] if trait in traits]
            self.part_terrain[part] = [
                change_record(dict(Id=trait, TraitTypeIndex=traits[trait][1], TraitValue=traits[trait][2]))
                for (trait,) in trait_sets[row[3]] if trait in traits and traits[trait][1] in TERRAIN_CHANGE_TYPES
            ]
        self.weapon_limits = {}
        for material, level, *_ in tables['m_weapon_level_up_material_set_content']:
            self.weapon_limits[material] = max(level + 1, self.weapon_limits.get(material, 1))
        self.restrictions = rows_grouped(tables['m_stage_sortie_restriction_set_content'])
        self.restriction_groups = rows_grouped(tables['m_stage_sortie_restriction_set_group_content'])
        self.series = rows_by(tables['m_series'])
        self.lineage = rows_by(tables['m_lineage'])
        self.part_lineage = rows_grouped(tables['m_option_parts_lineage'])
        self.series_sets = rows_grouped(tables['m_series_set'])
        self.unit_tags = rows_grouped(tables['m_unit_lineage'])
        self.pilot_tags = rows_grouped(tables['m_character_lineage'])
        self.unit_pilots = dict(tables['m_linked_character_unit'])
        self.pilot_units = {pilot: unit for unit, pilot in self.unit_pilots.items()}
        mechanisms = rows_by(tables['m_mechanism'])
        self.mechanism_types = {key: {mechanisms[mechanism][4] for mechanism, _ in rows}
                                for key, rows in rows_grouped(tables['m_mechanism_set']).items()}
        self.forms = defaultdict(list)
        for unit in self.units.values():
            if unit[23]:
                self.forms[unit[23]].append(unit[0])

    def name(self, kind, identifier):
        table, field = {
            'unit': (self.units, 6), 'pilot': (self.pilots, 4), 'supporter': (self.supporters, 1),
            'op': (self.parts, 5), 'weapon': (self.weapons, 1), 'series': (self.series, 2),
            'lineage': (self.lineage, 1),
        }[kind]
        prefix = {'pilot': 'character', 'op': 'option_parts'}.get(kind, kind)
        row = table.get(identifier)
        if row and (name := formation_names().get('m_' + prefix, {}).get(str(row[field]))):
            return name
        return str(identifier)

    def playable(self, kind):
        if kind == 'unit':
            return (key for key, row in self.units.items() if row[15] and row[23] == key)
        if kind == 'pilot':
            return (key for key, row in self.pilots.items() if row[1])
        if kind == 'supporter':
            return self.supporters
        return self.parts

    def mecha_koma(self, unit):
        return self.units[unit][4] if unit in self.units else ''

    def unit_role(self, identifier):
        row = self.units.get(identifier)
        return row[2] if row else 0

    def pilot_role(self, identifier):
        row = self.pilots.get(identifier)
        return row[12] if row else 0

    @lru_cache(maxsize=128)
    def _leader_predicates(self, condition):
        return tuple((group, *(tuple(map(int, re.findall(r'\d+', field))) for field in (tags, roles, series)))
                     for group, tags, roles, series in self.leader_conditions.get(condition, ()))

    def _leader_condition(self, condition, *, short=False):
        groups = self._leader_predicates(condition)
        use_or = leader_uses_or((row[0] for row in groups),
                                sum(len(field) for row in groups for field in row[1:]))
        expressions = []
        for _, tags, roles, series in groups:
            tag_labels = [f'[{self.name("lineage", key)}]' for key in tags]
            role_labels = [f'[{ROLES.get(key, str(key))}]' for key in roles]
            series_labels = []
            for key in series:
                name = self.name('series', key)
                if short:
                    name = name.removeprefix('机动战士高达').strip() or name
                series_labels.append(f'[{name}]')
            if use_or:
                expressions.extend((*tag_labels, *role_labels, *series_labels))
            else:
                role_expr = '/'.join(role_labels)
                if len(role_labels) > 1 and (tags or series or len(groups) > 1):
                    role_expr = f'({role_expr})'
                expressions.extend(filter(None, (*tag_labels, role_expr, *series_labels)))
        return ('/' if use_or else '&').join(dict.fromkeys(expressions))

    def _supporter_leader(self, identifier, step):
        leader, active = next(((leader, active) for rank, leader, active in self.supporter_steps.get(identifier, ())
                               if rank == step), (0, 0))
        definition = self.leader_skills.get(leader)
        contents = self.leader_contents.get(definition[1], ()) if definition else ()
        return definition, sorted(contents, key=lambda row: row[3]), active

    @lru_cache(maxsize=256)
    def supporter_badges(self, identifier, step):
        if identifier not in self.supporters:
            return ''
        _, contents, _ = self._supporter_leader(identifier, step)
        return ' · '.join(dict.fromkeys(filter(None, (
            self._leader_condition(condition, short=True) for _, _, condition, _ in contents))))

    def receives_leader_bonus(self, unit, supporter, step):
        if unit not in self.units or supporter not in self.supporters:
            return False
        _, contents, _ = self._supporter_leader(supporter, step)
        row = self.units[unit]
        tags = {tag for (tag,) in self.unit_tags.get(unit, ())}
        series = {key for key, _ in self.series_sets.get(row[22], ())}
        for _, _, condition, _ in contents:
            groups = self._leader_predicates(condition)
            predicates = [(group, set(required_tags), set(roles), set(required_series))
                          for group, required_tags, roles, required_series in groups]
            if (predicates or not condition) and leader_matches(predicates, tags, series, row[2]):
                return True
        return False

    def supporter_info(self, identifier, level, step):
        row = self.supporters[identifier]
        names = formation_names()
        definition, contents, active = self._supporter_leader(identifier, step)
        descriptions = names.get('m_supporter_leader_skill_content', {}) | names.get('m_supporter_leader_skill', {})
        leaders = tuple((self._leader_condition(condition), descriptions.get(str(text), ''))
                        for _, text, condition, _ in contents)
        skill = None
        if active_row := self.active_support_skills.get(active):
            text = names.get('m_supporter_active_skill_trait', {}) | names.get('m_supporter_active_skill', {})
            title = text.get(str(active_row[4])) or '支援技能'
            description = text.get(str(active_row[5]), '').strip()
            skill = ProfileEntry(title, active_row[6], 0, (description,) if description else ())
        rate = self.supporter_growth[level, step]
        return SupporterInfo(skill, leaders, bool(definition and definition[6]),
                             additional_parameter(row[5], rate), additional_parameter(row[6], rate))

    def supporter_step(self, identifier, original_id, original_step):
        if identifier == original_id:
            return original_step
        return max((row[0] for row in self.supporter_steps.get(identifier, ())), default=0)

    def pilot_numbers(self, identifier, level, *, specialized=False):
        pilot = self.pilots.get(identifier)
        if not pilot or not (row := self.pilot_status.get(pilot[20])):
            return ()
        start = 11 if specialized else 1
        growth = self.pilot_growth[level]
        ratings = [base + int((cap - base) * growth / 10000)
                   for base, cap in zip(row[start:start + 5], row[start + 5:start + 10])]
        corrections = Counter()
        rating_traits = []
        chance_steps = 0
        for key, unlock in self._pilot_configuration(self.pilot_abilities, identifier, specialized):
            if level < unlock or not (ability := self.ability_info.get(key)):
                continue
            for (trait,) in self.trait_sets.get(ability[2], ()):
                data = self.traits[trait]
                rating_traits.append(RatingTrait(data[1], data[11]))
                if data[1] == 19:
                    chance_steps += data[2]
                if data[4] == 0 and not data[6] and data[7] in self.self_condition_sets:
                    corrections[data[1]] += data[2]
        numbers = [(name, character_stat(base, corrections[kind], corrections[kind + 1]), base)
                   for name, base, kind in zip(('射击', '格斗', '守备', '反应', '觉醒'), ratings, range(21, 31, 2))]
        # The original rating points order defense, reaction and awaken differently from the trait enum.
        ranged, melee, defense, reaction, awaken = (
            stat_point(base, aggregate(rating_traits, flat), aggregate(rating_traits, rate))
            for base, (flat, rate) in zip(ratings, ('OP', 'QR', 'ST', 'UV', 'WX')))
        power = ((ranged + melee + awaken) / 3 + (defense + reaction) * .5) * self.pilot_power_coefficient / 100
        power = round_up_double(aggregate(rating_traits, 'A') + (1 + aggregate(rating_traits, 'B')) * power)
        numbers += [('SP', self.pilot_sp + corrections[20], self.pilot_sp),
                    ('战斗力', power, power), ('额外行动', max(1, chance_steps), 1)]
        return numbers

    def _condition_names(self, condition):
        tags, roles, series = {}, {}, {}
        for unit_tags, unit_roles, unit_series, character_tags, character_series in self.trait_conditions.get(condition, ()):
            for result, fields, lookup in (
                (tags, (unit_tags, character_tags), lambda key: self.name('lineage', key)),
                (roles, (unit_roles,), lambda key: ROLES.get(key, '')),
                (series, (unit_series, character_series), lambda key: self.name('series', key)),
            ):
                result.update((key, lookup(key)) for key in map(int, re.findall(r'\d+', ' '.join(fields))))
        return {kind: '、'.join(filter(None, entries.values()))
                for kind, entries in (('标签', tags), ('类型', roles), ('系列', series))}

    def _ability_effects(self, trait_set):
        names = formation_names().get('m_trait', {})
        effects = []
        for (trait,) in self.trait_sets.get(trait_set, ()):
            row = self.traits.get(trait)
            if not row or not (text := names.get(str(row[3]), '').strip()):
                continue
            conditions = self._condition_names(row[6])
            text = re.sub(r'上述“(标签／系列|标签|系列|类型)”',
                          lambda m: '「' + '／'.join(filter(None, (conditions[k] for k in m[1].split('／')))) + '」'
                          if any(conditions[k] for k in m[1].split('／')) else m[0], text)
            effects.append(text)
        return tuple(dict.fromkeys(effects))

    def _pilot_configuration(self, rows, identifier, specialized):
        now = int(datetime.now(timezone.utc).timestamp() * 1000)
        for _, key, level, sp_key, schedule in sorted(rows.get(identifier, ())):
            period = self.schedules.get(schedule)
            if specialized or not schedule or (period and period[1] <= now <= period[2]):
                yield (sp_key or key) if specialized else key, level

    def pilot_entries(self, identifier, *, specialized=False):
        names = formation_names()
        skills = []
        skill_text = names.get('m_character_skill', {})
        trait_text = names.get('m_character_skill_trait', {})
        for key, level in self._pilot_configuration(self.pilot_skills, identifier, specialized):
            if not (row := self.skill_info.get(key)):
                continue
            description = skill_text.get(str(row[2]), '').strip()
            effects = (description,) if description else tuple(dict.fromkeys(
                text for (trait,) in self.skill_trait_sets.get(row[5], ())
                if (source := self.skill_traits.get(trait)) and (text := trait_text.get(str(source[7]), '').strip())))
            costs = tuple(f'{label} {value}' for label, value in zip(('SP', 'EN', 'MP'), (row[7], row[20], row[21]))
                          if value)
            if row[19]:
                costs += (f'最多使用 {row[19]} 次',)
            skills.append(ProfileEntry(skill_text.get(str(row[1])) or f'技能 {key}', row[3], level, effects, costs))

        abilities = []
        for key, level in self._pilot_configuration(self.pilot_abilities, identifier, specialized):
            if row := self.ability_info.get(key):
                fallback = 'EX角色能力' if row[3] == 2 else f'能力 {key}'
                abilities.append(self._ability_entry(row, level, fallback))
        return skills, abilities

    def _ability_entry(self, row, level, fallback):
        detail = self.trait_details.get(row[2])
        text = formation_names().get('m_trait_set_detail', {})
        name = text.get(str(detail[3])) if detail else ''
        effects = self._ability_effects(row[2])
        if not effects and detail and (description := text.get(str(detail[5]), '').strip()):
            effects = (description,)
        return ProfileEntry(name or fallback, row[1] or (detail[1] if detail else ''), level, effects)

    def unit_numbers(self, identifier):
        unit = self.units[identifier]
        if status := self.unit_status.get(unit[33]):
            return tuple(zip(('HP', 'EN', '攻击', '防御', '机动', '移动'), status[1:7]))
        return ()

    def unit_entries(self, identifier):
        names = formation_names()
        weapons = []
        ssp_only = {row[0] for row in self.ssp_weapons.get(identifier, ())}
        for weapon_id, *_ in sorted(self.unit_weapons.get(identifier, ()), key=lambda row: row[1]):
            if weapon_id in ssp_only:
                continue
            weapon = self.weapons[weapon_id]
            label = self.name('weapon', weapon_id)
            weapon_type = {2: 'EX', 3: '地图'}.get(weapon[2])
            if weapon_type and weapon_type not in label:
                label = f'[{weapon_type}] {label}'
            facts, effects = self._weapon_profile(weapon)
            weapons.append(ProfileEntry(label, '', 1, effects, facts))
        abilities = [self._ability_entry(row, 0, f'能力 {key}')
                     for _, key in sorted(self.unit_abilities.get(identifier, ()))
                     if (row := self.ability_info.get(key))]
        skills = []
        skill_text = names.get('m_unit_skill', {})
        for _, key in sorted(self.unit_skill_sets.get(identifier, ())):
            if row := self.unit_skills.get(key):
                title = skill_text.get(str(row[1])) or f'技能 {key}'
                description = skill_text.get(str(row[2]), '').strip()
                costs = (f'最多使用 {row[7]} 次',) if row[7] else ()
                skills.append(ProfileEntry(title, row[3], 0, (description,) if description else (), costs))
        return weapons, abilities, skills

    def _weapon_profile(self, weapon):
        status = self.weapon_status.get(weapon[16])
        if not status:
            return (), ()
        growth = self.weapon_growth.get(status[9], (0, 0, 0))
        level = self.weapon_level_one.get(status[14] or growth[2])
        power, en, hit, critical = status[3:7]
        if level:
            power, en, hit, critical = (truncdiv(value * rate, 100)
                                        for value, rate in zip((power, en, hit, critical), level[:4]))
        minimum, maximum = status[1:3]
        facts = []
        if maximum:
            facts.append(f'射程 {minimum}–{maximum}' if maximum != minimum else f'射程 {maximum}')
        if power:
            facts.append(f'威力 {power:,}')
        if en:
            facts.append(f'EN {en}')
        if level:
            for label, value, unit in (('弹药', level[4] if weapon[2] == 3 else 0, ''),
                                       ('MP', level[5], ''), ('消耗 HP', level[6], '%')):
                if value:
                    facts.append(f'{label} {value}{unit}')
        facts.extend((f'命中 {hit}%', f'暴击 {critical}%'))
        text = formation_names().get('m_weapon_trait', {})
        effects = tuple(dict.fromkeys(
            effect for _, key in sorted(self.weapon_traits_level_one.get(status[15] or growth[1], ()))
            if (trait := self.weapon_traits.get(key)) and (effect := text.get(str(trait[3]), '').strip())))
        return tuple(facts), effects

    @lru_cache(maxsize=1024)
    def part_effects(self, part):
        names = formation_names().get('m_trait', {})
        effects = []
        for description, tags in self.part_traits.get(part, ()):
            if text := names.get(str(description), '').strip():
                if tags:
                    tag_names = '、'.join(self.name('lineage', tag) for tag in dict.fromkeys(tags))
                    text = text.replace('上述“标签”', f'标签「{tag_names}」')
                effects.append(text)
        row = self.parts.get(part)
        if row:
            if row[9]:
                effects.append(f'限定机体 · {self.name("unit", row[9])}')
            if row[7]:
                effects.append(f'适用系列 · {self.name("series", row[7])}')
            if tags := self.part_lineage.get(part):
                effects.append('适用标签 · ' + '、'.join(self.name('lineage', tag) for (tag,) in tags))
        return tuple(dict.fromkeys(effects))
    def can_sp(self, unit):
        row = self.units[unit]
        return row[14] < 5 and not row[35]

    def unit_max_level(self, unit, sp):
        return max(self.unit_exp) if sp else self.unit_limits[self.units[unit][14]]

    def pilot_max_level(self, pilot):
        return self.pilot_limits[self.pilots[pilot][11]]

    def linked_pilot(self, unit):
        return self.unit_pilots.get(unit) or self.unit_pilots.get(self.units[unit][23])

    def supporter_max_level(self, supporter):
        return self.supporter_limits[self.supporters[supporter][4]]

    def initial_cores(self, unit):
        config = self.ssp_config.get(unit)
        return [self.ssp_cores[core] for _, function, core in self.ssp_release.get(config[1], ())
                if function == 6 and core in self.ssp_cores] if config else []

    def equipped_cores(self, unit, points):
        if points is None:
            return self.initial_cores(unit)
        config = self.ssp_config.get(unit)
        return [core for key, point in points.items()
                if config and (core := self.ssp_cores.get(key)) and core[1] == config[2] and point >= core[3]]

    def member_weapons(self, unit, ssp, points=None):
        """Weapon choices for one form, including any original starting SSP grant."""
        ssp_keys = {row[0] for row in self.ssp_weapons.get(unit, ())}
        keys = [row[0] for row in sorted(self.unit_weapons.get(unit, ()), key=lambda row: row[1])]
        keys = [key for key in keys if key not in ssp_keys]
        if ssp:
            for core in self.equipped_cores(unit, points):
                for _, function, target in self.core_functions.get(core[6], ()):
                    if function != 5 or target not in self.core_weapons:
                        continue
                    _, before, after = self.core_weapons[target]
                    if before in keys:
                        keys[keys.index(before)] = after
                    elif not before and after in ssp_keys:
                        keys.append(after)
        result = {}
        for key in keys:
            _, _, _, _, _, main, _, _, _, material, *_ = self.weapons[key]
            # The native SSP grant creates its extra attack at the highest level.
            limit = self.weapon_limits.get(material, 1)
            result[main] = (self.name('weapon', key), limit, key in ssp_keys)
        return result

    def loadout_weapons(self, member):
        choices = {}
        unit = self.units[member.unit]
        for form in self.forms.get(unit[23], [unit[0]]):
            choices.update(self.member_weapons(form, member.ssp, member.ssp_cores))
        return choices

    def capabilities(self, member, form=None):
        unit = self.units[form or member.unit]
        terrain_id = unit[18]
        config = self.ssp_config.get(unit[0]) if member.ssp else None
        base_config = self.ssp_config.get(member.unit)
        if config and base_config and config[2] == base_config[2]:
            for core in self.equipped_cores(member.unit, member.ssp_cores):
                for _, function, target in self.core_functions.get(core[6], ()):
                    if function == 4 and target in self.terrain:
                        terrain_id = target
        values = self.terrain[terrain_id][1:]
        return upgrade(dict(enumerate(values, 2)), self.part_terrain.get(member.op, ()))

    def sortie_form(self, member, stage_id):
        """Use the native order: keep a legal form, then try transformation, then conversion."""
        stage = self.stages.get(stage_id)
        if not stage:
            return member.unit
        terrain = stage[12]
        current = member.unit
        if sortie_status(self.capabilities(member), terrain) != 2:
            return current
        unit = self.units[current]
        kinds = self.mechanism_types.get(unit[21], ())
        for kind in (2, 6):  # Transformation, Conversion. Escape and separation are combat-only.
            if kind in kinds:
                for form in self.forms[unit[23]]:
                    if form != current and sortie_status(self.capabilities(member, form), terrain) != 2:
                        return form
        return None

    def terrain_label(self, stage_id):
        return TERRAIN_NAMES[self.stages[stage_id][12]] if stage_id in self.stages else '未知'

    def restriction_failures(self, stage, force, position, unit, pilot):
        for target, require_all, group in self.restrictions.get(stage[22 + force], ()):
            for kind, row, fields, tags in (
                (1, unit, (22, 2), self.unit_tags), (2, pilot, (15, 12), self.pilot_tags),
            ):
                if not target & kind:
                    continue
                checks = []
                labels = []
                series = {key for key, _ in self.series_sets[row[fields[0]]]}
                for rule, key, slot in self.restriction_groups[group]:
                    if slot and slot != position:
                        continue
                    checks.append({
                        1: key in series, 2: [key] in tags[row[0]], 3: key == row[fields[1]], 4: key == row[0],
                    }.get(rule, True))
                    labels.append(self.name('series', key) if rule == 1 else f'标签 {key}' if rule == 2
                                  else self.name('unit' if kind == 1 else 'pilot', key) if rule == 4
                                  else f'类型 {key}')
                if checks and not (all(checks) if require_all else any(checks)):
                    subject = '机体' if kind == 1 else '驾驶员'
                    yield f'{subject}须符合' + ('、'.join(labels) if require_all else '／'.join(labels))


@lru_cache(maxsize=2)
def catalog_for(master):
    return FormationCatalog(master)


def current_catalog():
    return catalog_for(UPDATED_MASTER if UPDATED_MASTER.is_file() else BASE_MASTER)


def read_player_database():
    return {name: msgpack.unpackb(payload, raw=False)
            for name, payload in packed_tables(BASE_STAGE_DIR / 'PlayerDatabase.bytes')}


def default_team(catalog):
    tables = read_player_database()
    group = tables['user_group_formation'][0][1]
    slots = rows_by(tables['user_group_formation_unit'], 1)
    units = rows_by(tables['user_unit'], 1)
    pilots = rows_by(tables['user_character'], 1)
    supporters = rows_by(tables['user_supporter'], 1)
    supporter_slots = rows_by(tables['user_group_formation_supporter'], 1)
    weapon_levels = defaultdict(dict)
    for _, user_unit, weapon, level in tables['user_weapon']:
        weapon_levels[user_unit][weapon] = level
    team = [Force([], 0, 1), Force([], 0, 1)]
    for force, _, idx in catalog.group_slots[group]:
        _, _, user_unit, pilot, unit = slots[idx]
        data = units[user_unit]
        unit = unit or data[2]
        member = Member(unit, pilot, level_for(catalog.unit_exp, data[7]),
                        level_for(catalog.pilot_exp, pilots[pilot][2]), data[3], data[6], False, 1, data[4], {})
        member.weapons = {key: min(limit, weapon_levels[user_unit].get(key, 1))
                          for key, (_, limit, fixed) in catalog.loadout_weapons(member).items() if not fixed}
        team[force - 1].members.append(member)
    for force, _, idx in catalog.group_supporters[group]:
        supporter = supporters[supporter_slots[idx][2]]
        team[force - 1].supporter = supporter[2]
        team[force - 1].supporter_level = level_for(catalog.supporter_exp, supporter[3])
    return team


def formation_from_snapshot(snapshot, local_team):
    stage_id = snapshot['stage_id']
    catalog = catalog_for(master_for_stage(stage_id))
    stage = catalog.stages.get(stage_id)
    if stage and stage[1] == INVASION_STAGE_TYPE:
        raise ValueError('检查点没有强敌来袭的楼层资料。')
    groups = [[], []]
    for row in sorted(snapshot['units'], key=lambda unit: unit['step_order']):
        if row['side'] != 0 or int(row['npc_id']):
            continue
        group = row['formation_id']
        if group not in (1, 2):
            raise ValueError('检查点的编队槽位无法对应到本地两支部队。')
        parts = [int(part['option_part_id']) for part in row['option_parts'] if int(part['option_part_id'])]
        if len(parts) > 1:
            raise ValueError('本地编队每个槽位支持一个 OP。')
        points = {int(core['core_id']): core['core_point'] for core in row['ssp_cores'] if int(core['core_id'])}
        ssp_level = row['unit_ssp_level']
        member = Member(
            unit=int(row['master_unit_id']), pilot=int(row['character_id']), level=row['unit_level'],
            pilot_level=row['character_level'], stars=row['unit_limit_break'], sp=row['unit_specialized'],
            ssp=bool(ssp_level), ssp_level=max(1, ssp_level), op=next(iter(parts), 0), weapons={},
            ssp_cores=points if ssp_level else None, pilot_specialized=row['pilot_specialized'],
        )
        if member.unit in catalog.units:
            choices = catalog.loadout_weapons(member)
            for weapon in row['weapons']:
                key, level = int(weapon['main_weapon_id']), weapon['weapon_level']
                if key not in choices or choices[key][2] and choices[key][1] != level:
                    raise ValueError(f'本地战斗资料无法重建武装 {key} 的等级 {level}。')
                if not choices[key][2]:
                    member.weapons[key] = level
        groups[group - 1].append(member)
    if len(local_team) != 2 or any(len(members) != 5 for members in groups):
        raise ValueError('本地战斗后端需要两支各五个槽位的编队。')
    team = [replace(force, members=members) for force, members in zip(local_team, groups)]
    for row in snapshot.get('supporters', ()):
        force = team[row['force'] - 1]
        force.supporter = row['master_id']
        force.supporter_level = level_for(catalog.supporter_exp, row['exp'])
    if errors := validation_errors(team, catalog, stage_id):
        raise ValueError(describe_error(errors[0], team, catalog))
    return team


def validation_errors(team, catalog, stage_id=None):
    """Return (force, slot, reason). Slot 0 denotes the force's supporter."""
    stage = catalog.stages.get(stage_id)
    pilots = Counter(member.pilot for force in team for member in force.members if member.unit)
    errors = []
    if stage_id is not None and stage is None:
        return [(0, 0, '缺少该关卡的原版出击数据')]
    has_sortie_unit = False
    for force_number, force in enumerate(team, 1):
        if force.supporter:
            if force.supporter not in catalog.supporters:
                errors.append((force_number, 0, '支援人员在当前关卡资源中不可用'))
            elif not 1 <= force.supporter_level <= (cap := catalog.supporter_max_level(force.supporter)):
                errors.append((force_number, 0, f'支援人员等级须在 1–{cap} 之间'))
        sortie_count = stage[20 + force_number] if stage else 5
        for slot, member in enumerate(force.members, 1):
            if not member.unit:
                continue
            has_sortie_unit |= slot <= sortie_count
            failures = []
            unit, pilot = catalog.units.get(member.unit), catalog.pilots.get(member.pilot)
            if not unit or not unit[15]:
                failures.append('机体在当前关卡资源中不可用')
            if not pilot or not pilot[1]:
                failures.append('驾驶员在当前关卡资源中不可用')
            if pilots[member.pilot] > 1:
                failures.append('同一驾驶员重复编入')
            if member.op and member.op not in catalog.parts:
                failures.append('OP 在当前关卡资源中不可用')
            if pilot and not 1 <= member.pilot_level <= (cap := catalog.pilot_max_level(member.pilot)):
                failures.append(f'驾驶员等级须在 1–{cap} 之间')
            if unit:
                linked_pilot = catalog.linked_pilot(member.unit)
                if linked_pilot and member.pilot != linked_pilot:
                    failures.append(f'须搭配 {catalog.name("pilot", linked_pilot)}')
                elif (linked_unit := catalog.pilot_units.get(member.pilot)) and linked_unit not in (unit[0], unit[23]):
                    failures.append(f'该驾驶员须搭乘 {catalog.name("unit", linked_unit)}')
                cap = catalog.unit_max_level(member.unit, member.sp)
                if not 1 <= member.level <= cap:
                    failures.append(f'机体等级须在 1–{cap} 之间')
                if not 0 <= member.stars <= (catalog.max_stars if unit[37] else 0):
                    failures.append('机体星级超出原版范围')
                if member.sp and not catalog.can_sp(member.unit):
                    failures.append('该机体无法 SP 化')
                if member.ssp and member.unit not in catalog.ssp_config:
                    failures.append('该机体无法 SSP 化')
                if member.ssp and not 1 <= member.ssp_level <= max(catalog.ssp_exp, default=0):
                    failures.append('SSP 等级超出原版范围')
                if member.ssp_cores:
                    config = catalog.ssp_config.get(member.unit)
                    if not member.ssp or not config or any(
                        not (core := catalog.ssp_cores.get(key)) or core[1] != config[2] or point < 0
                        for key, point in member.ssp_cores.items()
                    ):
                        failures.append('SSP 核心不适用于该机体')
                choices = catalog.loadout_weapons(member)
                if any(key not in choices or choices[key][2] or not 1 <= level <= choices[key][1]
                       for key, level in member.weapons.items()):
                    failures.append('武装或武装等级不适用于该机体')
                if (part := catalog.parts.get(member.op)) and part[9] and part[9] not in (unit[0], unit[23]):
                    failures.append('此 OP 仅限对应机体装备')
                if stage and slot <= sortie_count and catalog.sortie_form(member, stage_id) is None:
                    failures.append(f'无法适应{catalog.terrain_label(stage_id)}地形')
                if stage and slot <= sortie_count and pilot:
                    failures.extend(catalog.restriction_failures(stage, force_number, slot, unit, pilot))
            if failures:
                errors.append((force_number, slot, '、'.join(failures)))
    if not has_sortie_unit:
        errors.append((0, 0, '请在关卡可出击的槽位编入至少一台机体'))
    return errors


def describe_error(error, team, catalog):
    force, slot, reason = error
    if not force:
        return reason
    subject = catalog.name('unit', team[force - 1].members[slot - 1].unit) if slot else '支援人员'
    return f'部队 {force} · {slot} 号位 {subject} · {reason}' if slot else f'部队 {force} · {subject} · {reason}'


def write_player_inputs(directory, team, stage, master, *, saved_supporters=None):
    catalog = catalog_for(master)
    if errors := validation_errors(team, catalog, stage):
        raise ValueError(describe_error(errors[0], team, catalog))
    original = read_player_database()
    group = list(original['user_group_formation'][0])
    user, group_id = group[:2]
    group[2:4] = ['本地编队', True]
    tables = defaultdict(list, user_group_formation=[group])
    original_slots = rows_by(original['user_group_formation_unit'], 1)
    original_units = rows_by(original['user_unit'], 1)
    original_pilots = rows_by(original['user_character'], 1)
    original_supporters = rows_by(original['user_supporter'], 1)
    supporter_slots = rows_by(original['user_group_formation_supporter'], 1)
    parts = Counter()
    expected = []
    for force, slot, idx in catalog.group_slots[group_id]:
        member = team[force - 1].members[slot - 1]
        if not member.unit:
            continue
        form = catalog.sortie_form(member, stage) or member.unit
        instance = original_slots[idx][2]
        old_unit = original_units[instance]
        unit = list(old_unit) if old_unit[2] == member.unit else [
            user, instance, member.unit, 0, 0, False, False, 0, 0, 0, 0, 0, 0]
        unit[3:8] = [member.stars, member.op, unit[5], member.sp, catalog.unit_exp[member.level]]
        tables['user_unit'].append(unit)
        pilot = list(original_pilots.get(member.pilot, [user, member.pilot, 0, 0, False, 0, 0, 0, False, False]))
        pilot[2] = catalog.pilot_exp[member.pilot_level]
        if member.pilot_specialized is not None:
            pilot[9] = member.pilot_specialized
        tables['user_character'].append(pilot)
        tables['user_group_formation_unit'].append([user, idx, instance, member.pilot, form])
        if member.op:
            parts[member.op] += 1
        if member.ssp:
            tables['user_unit_ssp'].append([user, instance, catalog.ssp_exp[member.ssp_level]])
            points = member.ssp_cores
            if points is None:
                points = {core[0]: core[3] for core in catalog.initial_cores(member.unit)}
            for core, point in points.items():
                tables['user_unit_ssp_custom_core'].append([user, instance, core, point])
        for weapon, level in member.weapons.items():
            tables['user_weapon'].append([user, instance, weapon, level])
        expected.append(f'{force - 1}\t{slot}\t{instance}\t{member.pilot}\t{form}\n')
    for force, _, idx in catalog.group_supporters[group_id]:
        row = team[force - 1]
        if not row.supporter:
            continue
        instance = supporter_slots[idx][2]
        old = original_supporters[instance]
        if saved_supporters is None:
            exp = catalog.supporter_exp[row.supporter_level]
            steps = catalog.supporter_step(row.supporter, old[2], old[4])
        else:
            instance, exp, steps = saved_supporters[force]
        tables['user_supporter'].append([user, instance, row.supporter, exp, steps, False, 0])
        tables['user_group_formation_supporter'].append([user, idx, instance])
    tables['user_option_parts'] = [[user, key, count, 0] for key, count in parts.items()]
    directory.mkdir()
    for table in (*original, 'user_unit_ssp', 'user_unit_ssp_custom_core'):
        tables.setdefault(table, [])
    # MasterMemory builds its primary-key indexes from the serialized row order.
    for name, rows in tables.items():
        key_size = 3 if name in ('user_weapon', 'user_unit_ssp_custom_core') else 2
        rows.sort(key=lambda row: row[:key_size])
    pack_memory_database(directory / 'PlayerDatabase.bytes',
                         {name: msgpack.packb(rows, use_bin_type=True) for name, rows in tables.items()})
    (directory / 'PlayerSelection.tsv').write_bytes((BASE_STAGE_DIR / 'PlayerSelection.tsv').read_bytes())
    (directory / 'PlayerExpected.tsv').write_text(''.join(expected), encoding='ascii')
    (directory / 'CustomFormation.enabled').write_text(f"{len(tables['user_supporter'])}\n", encoding='ascii')
