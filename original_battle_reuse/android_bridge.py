"""Connect to a running MuMu device and launch the standalone battle process."""

import json
import re
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path
from uuid import uuid4

from android_runner import build
from kernel_runner import prepare_battle_files, timeline_inputs

DEVICE_ROOT = '/data/local/tmp/gget-battle-reuse'
GAME_PACKAGE = 'com.bandainamcoent.gget_WW'
MUMU_CLIENT_VERSION = '2.8.0'
MUMU_RUNTIME_SHA = '92588f1b40791759be240affdf38317dd79995ad1c208e3891f889c508ebed0c'
MUMU_METADATA_SHA = 'faacda3708ce7a7fd7d2e72e0cfd2278111b748479737e235a5ce7b557aa0cb5'
METADATA_PATH = 'Data/Managed/Metadata/global-metadata.dat'
MUMU_RUNTIME_FILES = {
    'libil2cpp.so': MUMU_RUNTIME_SHA,
    'libunity.so': 'a42f0f459143754039e604895ddd8ecf3bf0dc96eaf72d548d3e312a6baedaef',
    'liblibsqlite3_sys.so': '5967486915b60fe7c151e5c2172973c97710a22e062b7465bb986d2a6279f9ab',
    METADATA_PATH: MUMU_METADATA_SHA,
}
MUMU_MASTER_TABLES = {
    'm_map_npc': 'npc.db', 'm_map_npc_ai': 'ai.db', 'm_map_npc_character': 'character.db',
    'm_map_npc_setting': 'setting.db', 'm_map_npc_unit': 'unit.db', 'm_reward': 'reward.db',
    'm_reward_set_content': 'reward_set.db',
}


def adb_shell(adb, script, *, timeout=20):
    result = subprocess.run([*adb, 'shell', '-T', script], capture_output=True, text=True,
                            encoding='utf-8', errors='replace', timeout=timeout,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise OSError(result.stderr.strip() or result.stdout.strip() or 'MuMu 执行命令失败')
    return result.stdout.strip()


def running_mumu_endpoints(adb):
    manager = shutil.which('MuMuManager.exe') or Path(adb).with_name('MuMuManager.exe')
    try:
        result = subprocess.run([str(manager), 'info', '--vmindex', 'all'], capture_output=True, text=True,
                                encoding='utf-8-sig', errors='replace', check=True, timeout=8,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        instances = json.loads(result.stdout)
    except (ValueError, OSError, subprocess.SubprocessError):
        return []
    if not isinstance(instances, dict):
        return []
    endpoints = []
    for instance in instances.values():
        if not isinstance(instance, dict) or not instance.get('is_android_started'):
            continue
        host, port = instance.get('adb_host_ip'), instance.get('adb_port')
        if host in ('127.0.0.1', 'localhost') and type(port) is int and 1 <= port <= 65535:
            endpoints.append(f'{host}:{port}')
    return endpoints


def find_mumu():
    adb = shutil.which('adb')
    if not adb:
        raise OSError('找不到 ADB，请检查 MuMu 或 Android 平台工具的安装。')

    def ready(endpoint):
        device = [adb, '-s', endpoint]
        try:
            if adb_shell(device, 'test -x /system/lib64/arm64/houdini64 && echo ready || true', timeout=5) == 'ready':
                return device
        except (OSError, subprocess.SubprocessError):
            pass

    result = subprocess.run([adb, 'devices'], capture_output=True, text=True, check=True, timeout=12,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    offline = set()
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) != 2 or not re.fullmatch(r'(127\.0\.0\.1|localhost):\d+', parts[0]):
            continue
        endpoint, state = parts
        if state == 'offline':
            offline.add(endpoint)
        elif state == 'device' and (device := ready(endpoint)):
            return device

    endpoints = running_mumu_endpoints(adb)
    for endpoint in endpoints:
        try:
            if endpoint in offline:
                subprocess.run([adb, 'disconnect', endpoint], capture_output=True, timeout=5,
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            subprocess.run([adb, 'connect', endpoint], capture_output=True, check=True, timeout=8,
                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except (OSError, subprocess.SubprocessError):
            continue
        if device := ready(endpoint):
            return device
    if endpoints:
        raise OSError(f'MuMu 正在运行，ADB 连接失败（{", ".join(endpoints)}）。请检查 MuMu 的 ADB 设置。')
    raise OSError('未检测到可用的 MuMu ADB 连接，请确认模拟器已启动并启用 ADB。')


def push(adb, source, target):
    result = subprocess.run([*adb, 'push', str(source), target], capture_output=True,
                            text=True, encoding='utf-8', errors='replace', timeout=90,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise OSError(result.stderr.strip() or result.stdout.strip() or f'无法传入 {source.name}')


def prepare_client_runtime(adb, suffix):
    shared = f'{DEVICE_ROOT}/runtime-{MUMU_RUNTIME_SHA[:12]}'
    missing = [name for name, checksum in MUMU_RUNTIME_FILES.items()
               if adb_shell(adb, f'sha256sum {shared}/{name} 2>/dev/null || true').split()[:1] != [checksum]]
    if not missing:
        return shared
    package_paths = adb_shell(adb, f'pm path {GAME_PACKAGE}').splitlines()
    paths = [line.removeprefix('package:') for line in package_paths if line.startswith('package:')]
    apks = {Path(path).name: path for path in paths}
    adb_shell(adb, f'mkdir -p {shared}/Data/Managed/Metadata')
    for name in missing:
        metadata = name == METADATA_PATH
        archive = apks.get('base.apk' if metadata else 'split_config.arm64_v8a.apk')
        if not archive:
            raise OSError('MuMu 未安装匹配的国际版 ARM64 游戏。')
        target = f'{shared}/{name}'
        temporary = f'{target}.{suffix}.tmp'
        member = f'assets/bin/{name}' if metadata else f'lib/arm64-v8a/{name}'
        try:
            adb_shell(adb, f'unzip -p {shlex.quote(archive)} {member} > {temporary}', timeout=45)
            if adb_shell(adb, f'sha256sum {temporary}').split()[:1] != [MUMU_RUNTIME_FILES[name]]:
                raise ValueError(f'游戏安装包中的 {Path(name).name} 与已验证的 {MUMU_CLIENT_VERSION} 版本不同。')
            adb_shell(adb, f'mv -f {temporary} {target}')
        finally:
            adb_shell(adb, f'rm -f {temporary}')
    return shared


def stage_mumu_master_tables(adb, session):
    root = f'/sdcard/Android/data/{GAME_PACKAGE}/files/master_data/sqlite_tables'
    for table, name in MUMU_MASTER_TABLES.items():
        db = adb_shell(adb, f'find {root}/{table} -maxdepth 1 -name "*.db" -type f 2>/dev/null || true').splitlines()
        if len(db) != 1:
            raise ValueError(f'MuMu 中没有找到唯一的游戏主表 {table}。')
        destination = f'{session}/{name}'
        adb_shell(adb, f'cp {shlex.quote(db[0])} {destination} && chmod 600 {destination}')


def prepare_mumu(stage, trace=False, *, invasion_level=0, progress=None, timeline_ui=False, resume=None,
                 formation=None, native_checkpoint=None, saved_supporters=None):
    if progress:
        progress(1, '连接 MuMu 并校验原版运行文件')
    adb = find_mumu()
    client_28 = native_checkpoint is not None or saved_supporters is not None
    root, log, pinned, _, inputs = prepare_battle_files('interactive', stage, trace, invasion_level=invasion_level,
                                                       formation=formation, saved_supporters=saved_supporters)
    inputs.update(timeline_inputs(log.parent, timeline_ui=timeline_ui, resume=resume))
    if native_checkpoint is not None:
        inputs['MuMuCheckpoint.bin'] = native_checkpoint
    # Houdini's SQLite bridge fails to open some tables with the shorter host log directory name.
    session_id = f'{stage}-{uuid4().hex}' if client_28 else log.parent.name
    session = f'{DEVICE_ROOT}/{session_id}'
    shared = f'{DEVICE_ROOT}/runtime-{pinned["libil2cpp.so"][:12]}'
    # Each battle has its own inputs and save directory. Only the pinned runtime is shared.
    runtime = {
        'libil2cpp.so': ('libil2cpp.so', pinned['libil2cpp.so']),
        'libunity.so': ('libunity.so', pinned['libunity.so']),
        'Data/Managed/Metadata/global-metadata.dat': (
            'global-metadata.packaged.dat', pinned['global-metadata.packaged.dat']),
    }
    if client_28:
        shared = prepare_client_runtime(adb, session_id)
        runtime = {}
    adb_shell(adb, f'mkdir -p {shared}/Data/Managed/Metadata')
    for relative, (name, digest) in runtime.items():
        target = f'{shared}/{relative}'
        if adb_shell(adb, f'sha256sum {target} 2>/dev/null || true').split()[:1] == [digest]:
            continue
        if progress:
            progress(1, f'首次传入 MuMu · {name}')
        temporary = f'{target}.{log.parent.name}.tmp'
        try:
            push(adb, root / 'game' / name, temporary)
            if adb_shell(adb, f'sha256sum {temporary}').split()[0] != digest:
                raise OSError(f'MuMu 收到的 {name} 校验失败')
            adb_shell(adb, f'mv -f {temporary} {target}')
        finally:
            adb_shell(adb, f'rm -f {temporary}')

    try:
        if progress:
            progress(2, '编译战斗入口并传入关卡数据')
        # The session name comes from the host's ASCII temporary-directory name.
        adb_shell(adb, f'mkdir -p {session}/stage {session}/save/GameData/UserData && '
                       f'ln -s {shared}/Data {session}/Data && '
                       f'ln -s {shared}/libil2cpp.so {session}/libil2cpp.so && '
                       f'ln -s {shared}/libunity.so {session}/libunity.so')
        if client_28:
            adb_shell(adb, f'ln -s {shared}/liblibsqlite3_sys.so {session}/liblibsqlite3_sys.so')
            stage_mumu_master_tables(adb, session)
        with tempfile.TemporaryDirectory(prefix='build-', dir=log.parent) as scratch:
            executable = Path(scratch) / 'battle_reuse'
            build(root, executable, android_bridge=True)
            push(adb, executable, f'{session}/battle_reuse')
        for name in ('master_database.bytes', 'EnglishLocalization.csv', 'OctoCatalog.bytes'):
            inputs.setdefault(name, root / 'game/stage' / name)
        for name, source in inputs.items():
            push(adb, source, f'{session}/stage/{name}')
        adb_shell(adb, f'chmod 700 {session}/battle_reuse')
    except (OSError, ValueError, subprocess.SubprocessError):
        try:
            remove_session(adb, session)
        except (OSError, subprocess.SubprocessError):
            pass
        raise

    launch = shlex.join([f'{session}/battle_reuse', log.stem, session])
    if client_28:
        launch = f'cd {session} && LD_LIBRARY_PATH={session} {launch} {MUMU_CLIENT_VERSION}'
    # The shell returns the guest exit code and removes the per-battle files on normal exit.
    command = [*adb, 'shell', '-T', f'{launch}; code=$?; rm -rf {session}; exit $code']
    return command, log, adb, session


def remove_session(adb, session):
    if re.fullmatch(re.escape(DEVICE_ROOT) + r'/\d+-[a-z0-9_]+', session):
        adb_shell(adb, f'rm -rf {session}')
