"""Copy the game's last persisted battle checkpoint and decode its import metadata."""

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import shlex
import shutil
import subprocess
from tempfile import TemporaryDirectory, mkdtemp
from uuid import uuid4
from android_bridge import (DEVICE_ROOT, GAME_PACKAGE as PACKAGE,
                            MUMU_CLIENT_VERSION as CLIENT_VERSION, adb_shell, find_mumu, prepare_client_runtime,
                            push, remove_session)
from android_runner import build
from prepare_android import ANDROID
from prepare_stage import stage_directory
from battle_paths import OUTPUTS
from stage_catalog import STAGE_NAMES


CHECKPOINT = (f'/sdcard/Android/data/{PACKAGE}/files/GameData/UserData/'
              '0B6C1A40DFFE3992946EE8420E602348')
DEFAULT_OUTPUT = OUTPUTS / 'battle_headless/emulator_snapshots'
RAW_NAME = 'IngameAllMemoryPackSaveData.bin'
MAX_CHECKPOINT = 64 * 1024 * 1024
ID_FIELDS = {'master_unit_id', 'character_id', 'npc_id', 'main_weapon_id', 'option_part_id', 'core_id'}
BOOL_FIELDS = {'leader', 'unit_specialized', 'pilot_specialized'}
TOWER_NAMES = {907001: '东塔', 907002: '西塔', 907003: '南塔'}
UNIT_LISTS = {'WEAPON': ('weapons', {'main_weapon_id', 'weapon_level'}),
              'PART': ('option_parts', {'option_part_id'}),
              'CORE': ('ssp_cores', {'core_id', 'core_point', 'core_release_point'})}


def adb_call(adb, *args, timeout=30):
    result = subprocess.run([*adb, *args], capture_output=True, timeout=timeout,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        message = (result.stderr or result.stdout).decode('utf-8', 'replace').strip()
        raise OSError(message or f'ADB {args[0]} 退出码 {result.returncode}')
    return result.stdout


def checkpoint_info(adb):
    remote = shlex.quote(CHECKPOINT)
    info = adb_shell(adb, f'test -f {remote} && stat -c "%s %Y" {remote} && sha256sum {remote} || echo missing')
    if info == 'missing':
        raise ValueError('MuMu 中没有找到游戏写盘的战斗检查点，请先进入战斗。')
    stat, checksum = info.splitlines()
    size, modified = map(int, stat.split())
    sha = checksum.split()[0]
    if not 0 < size <= MAX_CHECKPOINT or not re.fullmatch(r'[a-f0-9]{64}', sha):
        raise ValueError('游戏战斗检查点大小或校验值无效。')
    return size, modified, sha


def copy_checkpoint(adb, path):
    for _ in range(3):
        before = checkpoint_info(adb)
        data = adb_call(adb, 'exec-out', 'cat', CHECKPOINT)
        after = checkpoint_info(adb)
        if before == after and (len(data), sha256(data).hexdigest()) == (before[0], before[2]):
            path.write_bytes(data)
            return after
    raise ValueError('读取期间游戏正在更新存档，请停在战场地图后重试。')


def installed_version(adb):
    versions = adb_shell(adb, f'dumpsys package {PACKAGE} | grep versionName=')
    match = re.search(r'versionName=([^\s]+)', versions)
    if not match:
        raise ValueError('MuMu 中没有检测到游戏安装。')
    return match[1]


def stage_metadata(stage_id):
    manifest = stage_directory(stage_id) / 'manifest.json'
    result = {'stage_id': stage_id}
    if manifest.is_file():
        metadata = json.loads(manifest.read_bytes())
        if metadata['stage_id'] == stage_id:
            result['map_id'] = metadata['map_id']
    if stage_id in STAGE_NAMES:
        result['stage_name'] = STAGE_NAMES[stage_id]
    tower, floor = divmod(stage_id, 100)
    if tower in TOWER_NAMES and 1 <= floor <= 20:
        result['stage_name'] = f'{TOWER_NAMES[tower]} {floor}F'
    return result


def parse_snapshot(output):
    headers, units = {}, []
    unit, target = None, headers
    count, latest = None, None
    for line in output.splitlines():
        event, *values = line.split('\t')
        if event == 'COUNT':
            count = int(values[0])
        elif event == 'LATEST':
            latest = bool(int(values[0]))
        elif event == 'UNIT':
            unit = {name: [] for name, _ in UNIT_LISTS.values()}
            units.append(unit)
            target = unit
        elif event in UNIT_LISTS:
            target = {}
            unit[UNIT_LISTS[event][0]].append(target)
        elif event == 'FIELD':
            name, value = values
            number = int(value)
            target[name] = str(number) if name in ID_FIELDS else bool(number) if name in BOOL_FIELDS else number
    ids = {unit.get('id') for unit in units}
    if (not output.rstrip().endswith('SNAPSHOT_COMPLETE') or latest is None or not count or
            headers.get('stage_id', 0) <= 0 or 'stage_total_start_count' not in headers or
            len(units) != count or len(ids) != count or None in ids or
            any(row.keys() != fields for unit in units for name, fields in UNIT_LISTS.values()
                for row in unit[name]) or
            any(unit.get('option_part_count') != len(unit['option_parts']) for unit in units)):
        raise ValueError('原版反序列化器未返回完整的开场部署。')
    return headers, latest, units


def parse_progress(output):
    turns, supporters = [], []
    for line in output.splitlines():
        event, *values = line.split('\t')
        if event == 'PROGRESS':
            turn, side = map(int, values)
            turns.append({'turn': turn, 'side': side})
        elif event == 'SUPPORTER_PROGRESS':
            force, master, instance, exp, stars = map(int, values)
            supporters.append({'force': force, 'master_id': master, 'instance_id': instance,
                               'exp': exp, 'stars': stars})
    if len(turns) != 1 or turns[0]['turn'] <= 0:
        raise ValueError('检查点没有返回有效的战斗回合。')
    if (len(supporters) != 2 or {row['force'] for row in supporters} != {1, 2} or
            any(row['master_id'] <= 0 or not 0 < row['instance_id'] < 2**63 or
                row['exp'] < 0 or not 0 <= row['stars'] <= 32767 for row in supporters)):
        raise ValueError('检查点没有返回完整的两支部队支援者。')
    return turns[0], supporters


def run_decoder(adb, checkpoint, working, *, read_progress=False):
    executable = working / 'battle_reuse'
    build(ANDROID / 'rootfs', executable, android_bridge=True)
    session_id = f'0-snapshot{uuid4().hex}'
    session = f'{DEVICE_ROOT}/{session_id}'
    shared = prepare_client_runtime(adb, session_id)
    mode = 'snapshot-progress' if read_progress else 'snapshot'
    try:
        adb_shell(adb, f'mkdir -p {session} && cd {session} && '
                       f'ln -s {shared}/Data {shared}/libil2cpp.so {shared}/libunity.so .')
        for source, relative in ((executable, 'battle_reuse'), (checkpoint, 'checkpoint.bin')):
            push(adb, source, f'{session}/{relative}')
        return adb_shell(adb, f'chmod 700 {session}/battle_reuse && '
                         f'{session}/battle_reuse {mode} {session} {CLIENT_VERSION}', timeout=45)
    finally:
        remove_session(adb, session)


def export_snapshot(output_root, serial=None, *, raw_only=False, screenshot=False, read_progress=False):
    if serial is not None:
        adb_path = shutil.which('adb')
        if not adb_path:
            raise OSError('找不到 adb。')
        adb = [adb_path, '-s', serial]
    else:
        adb = find_mumu()
    version = installed_version(adb)
    output_root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    with TemporaryDirectory(prefix='.capture-', dir=output_root) as scratch:
        working = Path(scratch)
        checkpoint = working / RAW_NAME
        size, modified, checksum = copy_checkpoint(adb, checkpoint)
        # Keep the verified original even when the installed version cannot be decoded.
        output = Path(mkdtemp(prefix=f'{timestamp}-', dir=output_root))
        checkpoint = checkpoint.replace(output / RAW_NAME)
        source = {
            'client_version': version,
            'emulator': 'MuMu',
            'checkpoint_file': RAW_NAME,
            'checkpoint_bytes': size,
            'checkpoint_sha256': checksum,
            'checkpoint_modified_at': datetime.fromtimestamp(modified, timezone.utc).isoformat(),
        }
        snapshot = {'schema_version': 1, 'kind': 'native_checkpoint', 'source': source}
        try:
            if screenshot:
                image = adb_call(adb, 'exec-out', 'screencap', '-p')
                if not image.startswith(b'\x89PNG\r\n\x1a\n'):
                    raise ValueError('MuMu 未返回有效截图。')
                (output / 'battle-screen.png').write_bytes(image)
            if not raw_only:
                if version != CLIENT_VERSION:
                    raise ValueError(f'战斗检查点解码支持 {CLIENT_VERSION}，当前游戏版本为 {version}。')
                decoded = run_decoder(adb, checkpoint, working, read_progress=read_progress)
                headers, latest, units = parse_snapshot(decoded)
                resume = read_progress and latest
                if resume:
                    snapshot['progress'], snapshot['supporters'] = parse_progress(decoded)
                source['latest_step_present'] = latest
                snapshot.update(
                    kind='battle_progress' if resume else 'initial_deployment', can_resume_native_battle=resume,
                    **stage_metadata(headers['stage_id']),
                    stage_total_start_count=headers['stage_total_start_count'],
                    units=units,
                )
        except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
            snapshot['decode_error'] = str(exc)
        filename = 'initial-deployment.json' if snapshot['kind'] == 'initial_deployment' else 'checkpoint.json'
        (output / filename).write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        return output, filename, snapshot


def main():
    parser = argparse.ArgumentParser(description='只读导出 MuMu 游戏最近写盘的战斗检查点和开场部署。')
    parser.add_argument('--serial', help='ADB 设备序列号，默认自动检测 MuMu')
    parser.add_argument('--out', type=Path, default=DEFAULT_OUTPUT, help='导出根目录')
    parser.add_argument('--raw-only', action='store_true', help='只复制并校验原版检查点')
    parser.add_argument('--screenshot', action='store_true', help='同时保存当前屏幕截图')
    args = parser.parse_args()
    try:
        output, filename, snapshot = export_snapshot(
            args.out.resolve(), args.serial, raw_only=args.raw_only, screenshot=args.screenshot)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        parser.exit(1, f'{exc}\n')
    print(f'原版检查点已保存到 {output / RAW_NAME}')
    if error := snapshot.get('decode_error'):
        parser.exit(1, f'导出未全部完成：{error}\n')
    print(output / filename)


if __name__ == '__main__':
    main()
