"""Read and write desktop archives of the original battle timeline."""

import json
from pathlib import Path
import struct
from tempfile import NamedTemporaryFile
from zipfile import ZipFile, ZIP_DEFLATED

from prepare_stage import ARTIFACTS, stage_directory


ENTRY = 'BattleTimeline.bin'
MUMU_SUPPORTERS_ENTRY = 'MuMuSupporters.json'
PREVIEW_ENTRY = 'Preview.jpg'
MAX_PREVIEW = 128 * 1024
BATTLE_SAVES = ARTIFACTS / 'battle_saves'
MAGIC = b'GGETSV2\0'
HEADER = struct.Struct('<8sqqII')
CHECKPOINT = struct.Struct('<QQIIII')
RANDOM_STATE_SIZE = 3 * (1 + 624) * 4 + 4 * 4
CHECKPOINT_HEADER_SIZE = CHECKPOINT.size + RANDOM_STATE_SIZE + 4
UNIT_MORALE_SIZE = 8
MAX_CHECKPOINT = 64 * 1024 * 1024
MAX_TIMELINE = 1024 * 1024 * 1024


def timeline_entry(archive):
    entry = archive.getinfo(ENTRY)
    if not HEADER.size < entry.file_size <= MAX_TIMELINE:
        raise ValueError('存档大小无效')
    return entry


def read_header(source):
    data = source.read(HEADER.size)
    if len(data) != HEADER.size:
        raise ValueError('存档头不完整')
    magic, stage, invasion, count, reserved = HEADER.unpack(data)
    if magic != MAGIC or reserved or stage <= 0 or not count:
        raise ValueError('无法识别存档格式')
    return data, stage, invasion, count


def resolve_invasion_level(stage, invasion):
    if not invasion:
        return 0
    manifest = json.loads((stage_directory(stage) / 'manifest.json').read_bytes())
    level = next((int(level) for level, row in manifest.get('invasion_floors', {}).items() if row['id'] == invasion), 0)
    if not level:
        raise ValueError('存档对应的强敌楼层不可用')
    return level


def read_save_entry(path):
    with ZipFile(path) as archive:
        with archive.open(timeline_entry(archive)) as source:
            _, stage, invasion, _ = read_header(source)
        try:
            level = resolve_invasion_level(stage, invasion)
        except (OSError, ValueError, KeyError):
            level = None
        preview = None
        if PREVIEW_ENTRY in archive.namelist() and archive.getinfo(PREVIEW_ENTRY).file_size <= MAX_PREVIEW:
            preview = archive.read(PREVIEW_ENTRY)
        return preview, stage, level


def unpack_save(path, output):
    with ZipFile(path) as archive:
        entry = timeline_entry(archive)
        with archive.open(entry) as source:
            data, stage, invasion, count = read_header(source)
            output.write(data)
            remaining = entry.file_size - len(data)
            for _ in range(count):
                record = source.read(CHECKPOINT_HEADER_SIZE)
                if len(record) != CHECKPOINT_HEADER_SIZE:
                    raise ValueError('存档中的行动记录不完整')
                _, _, size, _, move, reserved = CHECKPOINT.unpack_from(record)
                unit_count = struct.unpack_from('<I', record, CHECKPOINT_HEADER_SIZE - 4)[0]
                if move > 1 or reserved or not 0 < size <= MAX_CHECKPOINT or not 0 < unit_count <= 65535:
                    raise ValueError('存档中的行动记录无效')
                size += unit_count * UNIT_MORALE_SIZE
                remaining -= len(record) + size
                if remaining < 0:
                    raise ValueError('存档中的行动记录不完整')
                output.write(record)
                while size:
                    chunk = source.read(min(size, 65536))
                    if not chunk:
                        raise ValueError('存档中的行动记录不完整')
                    output.write(chunk)
                    size -= len(chunk)
            if remaining:
                raise ValueError('存档包含多余数据')
    return stage, resolve_invasion_level(stage, invasion)


def read_saved_formation(path):
    from formation import TEAM_ENTRY, load_team

    with ZipFile(path) as archive:
        if TEAM_ENTRY not in archive.namelist():
            return None
        with archive.open(TEAM_ENTRY) as source:
            return load_team(source)


def read_saved_supporters(path):
    with ZipFile(path) as archive:
        if MUMU_SUPPORTERS_ENTRY not in archive.namelist():
            return None
        if archive.getinfo(MUMU_SUPPORTERS_ENTRY).file_size > 4096:
            raise ValueError('MuMu 存档的支援者资料过大')
        records = json.loads(archive.read(MUMU_SUPPORTERS_ENTRY))
    if not isinstance(records, dict) or records.keys() != {'1', '2'}:
        raise ValueError('MuMu 存档缺少出击支援者资料')
    for row in records.values():
        if (not isinstance(row, list) or len(row) != 3 or any(type(value) is not int for value in row) or
                not 0 < row[0] < 2**63 or not 0 <= row[1] < 2**63 or not 0 <= row[2] <= 32767):
            raise ValueError('MuMu 存档的支援者资料无效')
    return {int(force): tuple(row) for force, row in records.items()}


def write_save(destination, data, formation=None, *, preview=None, saved_supporters=None):
    destination = Path(destination)
    with NamedTemporaryFile(dir=destination.parent, suffix='.tmp', delete=False) as pending:
        temporary = Path(pending.name)
    try:
        with ZipFile(temporary, 'w', compression=ZIP_DEFLATED, compresslevel=1) as archive:
            if formation is not None:
                from formation import TEAM_ENTRY, encode_team

                archive.writestr(TEAM_ENTRY, encode_team(formation))
            if saved_supporters is not None:
                archive.writestr(MUMU_SUPPORTERS_ENTRY, json.dumps(saved_supporters))
            if preview:
                archive.writestr(PREVIEW_ENTRY, preview)
            with archive.open(ENTRY, 'w') as output:
                data.seek(0)
                while chunk := data.read(65536):
                    output.write(chunk)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
