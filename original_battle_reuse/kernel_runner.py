"""Boot the extracted ARM64 game libraries against a real Linux kernel."""

import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading

from android_runner import build
from prepare_android import ANDROID, ARTIFACTS, HERE, digest
from prepare_kernel import BIN, DOWNLOADS, EXECUTABLE, KERNEL
from prepare_stage import (STAGE, UPDATED_MASTER, UPDATED_OCTO, extract_additional_stage, pack_memory_database,
                           stage_directory, stage_prepared)
from runtime_version import PINNED_LIBRARY_SHA256

UNITY_SHA256 = '98230fdb63b66b23f8da21ccd04ad18df7e54fc01a01388b13dbbdacf34ba584'
METADATA_SHA256 = 'b914b8179298c136eab4a28e0e5f9ba2ad1c0af8c68a9a3df44fb497ddc04900'
INITRAMFS_CACHE_VERSION = 3
INITRAMFS_CACHE_LOCK = threading.Lock()


def firmware_module(directory):
    target = directory / 'qemu_fw_cfg.ko'
    linux = json.loads((KERNEL / 'manifest.json').read_bytes())['linux']
    package = DOWNLOADS / linux['archive']
    if digest(package) != linux['archive_sha256']:
        raise ValueError('The Linux kernel package differs from the prepared runtime')
    with tarfile.open(package, mode='r:gz', ignore_zeros=True) as archive:
        member = next((item for item in archive if item.name.endswith('/qemu_fw_cfg.ko.gz')), None)
        if member is None:
            raise ValueError('The pinned kernel package is missing qemu_fw_cfg.ko.gz')
        with archive.extractfile(member) as source:
            target.write_bytes(gzip.decompress(source.read()))
    return target


def build_init(directory):
    compiler = ARTIFACTS / 'zig_env/ziglang/zig.exe'
    target = directory / 'init'
    environment = os.environ.copy()
    environment['ZIG_GLOBAL_CACHE_DIR'] = str(ANDROID / 'zig_cache')
    environment['ZIG_LOCAL_CACHE_DIR'] = str(ANDROID / 'zig_local')
    subprocess.run([str(compiler), 'cc', '-target', 'aarch64-linux-musl', '-static', '-Os',
                    str(HERE / 'kernel_init.c'), '-o', str(target)],
                   env=environment, cwd=directory, check=True)
    return target


def initramfs_files(root, init, executable):
    files = {'init': init, 'bin/battle_reuse': executable, 'lib/qemu_fw_cfg.ko': firmware_module(init.parent)}
    for group in ('system/bin', 'system/lib64'):
        directory = root / group
        for source in directory.rglob('*'):
            if source.is_file():
                files[source.relative_to(root).as_posix()] = source
    shared_stage = root / 'game/stage'
    for name in ('master_database.bytes', 'EnglishLocalization.csv', 'OctoCatalog.bytes'):
        source = shared_stage / name
        if source.is_file():
            files[f'game/stage/{name}'] = source
    for name in ('libil2cpp.so', 'libunity.so', 'libmain.so', 'globalgamemanagers'):
        files['game/' + name] = root / 'game' / name
    files['game/Data/Managed/Metadata/global-metadata.dat'] = root / 'game/global-metadata.packaged.dat'
    return files


def write_initramfs(files, output, signature):
    directories = {'proc', 'sys', 'dev', 'tmp', 'game/save', 'game/stage'}
    for name in files:
        directories.update(parent.as_posix() for parent in Path(name).parents if parent.as_posix() != '.')
    with gzip.GzipFile(filename=signature, mode='wb', fileobj=output, compresslevel=1, mtime=0) as stream:
        inode = 0

        def write_entry(name, mode, source=None, device=(0, 0)):
            nonlocal inode
            inode += 1
            encoded = name.encode('ascii') + b'\0'
            size = source.stat().st_size if source else 0
            fields = (inode, mode, 0, 0, 2 if stat.S_ISDIR(mode) else 1, 0, size,
                      0, 0, *device, len(encoded), 0)
            stream.write(b'070701' + ''.join(f'{field:08x}' for field in fields).encode('ascii'))
            stream.write(encoded)
            stream.write(bytes((-(110 + len(encoded))) % 4))
            if source:
                with source.open('rb') as file:
                    shutil.copyfileobj(file, stream)
                stream.write(bytes((-size) % 4))

        for name in sorted(directories, key=lambda item: (item.count('/'), item)):
            write_entry(name, stat.S_IFDIR | 0o755)
        write_entry('dev/console', stat.S_IFCHR | 0o600, device=(5, 1))
        write_entry('dev/null', stat.S_IFCHR | 0o666, device=(1, 3))
        for name, source in sorted(files.items()):
            write_entry(name, stat.S_IFREG | (0o755 if name == 'init' or '/bin/' in '/' + name else 0o644), source)
        write_entry('TRAILER!!!', 0)


def cached_initramfs(files, verified_files):
    inputs = [(name, verified_files.get(path) or digest(path)) for name, path in sorted(files.items())]
    signature = hashlib.sha256(json.dumps([INITRAMFS_CACHE_VERSION, inputs]).encode('ascii')).hexdigest()
    directory = KERNEL / 'initramfs'
    target = directory / 'rootfs.cpio.gz'
    with INITRAMFS_CACHE_LOCK:
        try:
            with target.open('rb') as archive:
                header = archive.read(10)
                marker = signature.encode('ascii') + b'\0'
                if header[:4] == b'\x1f\x8b\x08\x08' and archive.read(len(marker)) == marker:
                    return target
        except FileNotFoundError:
            pass
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=directory, suffix='.tmp', delete=False) as pending:
            temporary = Path(pending.name)
        try:
            with temporary.open('wb') as output:
                write_initramfs(files, output, signature)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    return target


def invasion_player_database(stage, directory, stage_id, invasion_id):
    import msgpack

    contents = (stage / 'PlayerDatabase.bytes').read_bytes()
    unpacker = msgpack.Unpacker(raw=False)
    unpacker.feed(contents)
    header = unpacker.unpack()
    start = unpacker.tell()
    tables = {name: contents[start + offset:start + offset + size] for name, (offset, size) in header.items()}
    user_id = int((stage / 'PlayerSelection.tsv').read_text('ascii').split()[0])
    # UserStageRepository.FindStageInProgress reads this original user table to resolve the level.
    tables['user_stage_in_progress'] = msgpack.packb([[user_id, stage_id, True, 1, 0, invasion_id]])
    target = directory / 'PlayerDatabase.bytes'
    pack_memory_database(target, tables)
    return target


def prepare_battle_files(mode, stage_id, trace_nuls=False, *, invasion_level=0, formation=None, saved_supporters=None):
    load_exchange = mode in ('exchange', 'phase', 'enemy-phase', 'stage-run', 'interactive')
    load_player = load_exchange or mode in ('deployment', 'opening', 'strikes')
    load_roster = load_player or mode == 'roster'
    if trace_nuls and not load_roster:
        raise ValueError('--trace-nulls requires a mode that initializes the roster')
    root = ANDROID / 'rootfs'
    stage = root / 'game/stage'
    selected_stage = stage if stage_id == STAGE else stage_directory(stage_id)
    stage_manifest = selected_stage / 'manifest.json'
    load_scene = load_roster or mode == 'scene'
    if (load_scene or mode == 'stage') and not stage_manifest.is_file():
        raise ValueError(f'Run prepare_stage.py --stage {stage_id} --fetch-bundle to prepare the selected stage')
    if (mode == 'stage' or load_scene) and not (stage / 'master_database.bytes').is_file():
        raise ValueError('Run prepare_stage.py to assemble the original master database')
    if load_scene and (not (selected_stage / 'StageEntity.json').is_file() or not (stage / 'EnglishLocalization.csv').is_file()):
        raise ValueError('Run prepare_stage.py --fetch-bundle to extract the stage and localization assets')
    if load_roster and not (selected_stage / 'NpcExpected.tsv').is_file():
        raise ValueError('Run prepare_stage.py to extract the original NPC verification rows')
    if load_player and not all((stage / name).is_file()
            for name in ('PlayerDatabase.bytes', 'PlayerSelection.tsv', 'PlayerExpected.tsv')):
        raise ValueError('Run prepare_player.py to prepare the captured player formation')
    if load_exchange and not (stage / 'OctoCatalog.bytes').is_file():
        raise ValueError('Run prepare_stage.py to stage the original offline Octo index')
    pinned = {
        'libil2cpp.so': PINNED_LIBRARY_SHA256,
        'global-metadata.packaged.dat': METADATA_SHA256,
    }
    if load_scene:
        pinned['libunity.so'] = UNITY_SHA256
    verified_files = {}
    for name, expected in pinned.items():
        source = root / 'game' / name
        if not source.is_file() or digest(source) != expected:
            raise ValueError(f'Run prepare_android.py to extract the pinned {name}')
        verified_files[source] = expected
    directory = KERNEL if stage_id == STAGE else KERNEL / str(stage_id)
    if mode == 'interactive':
        sessions = KERNEL / 'interactive'
        sessions.mkdir(parents=True, exist_ok=True)
        directory = Path(tempfile.mkdtemp(prefix=f'{stage_id}-', dir=sessions))
    else:
        directory.mkdir(parents=True, exist_ok=True)
    stage_expected = player_database = master_override = None
    player_source = stage
    if load_scene or mode == 'stage':
        manifest = json.loads(stage_manifest.read_text('utf-8'))
        if manifest['stage_id'] != stage_id:
            raise ValueError('The selected stage manifest has the wrong ID')
        if stage_id != STAGE:
            master_hash = manifest['master_database']['sha256']
            if digest(stage / 'master_database.bytes') != master_hash:
                if not UPDATED_MASTER.is_file() or digest(UPDATED_MASTER) != master_hash:
                    raise ValueError('Prepared stage master database differs from the stage manifest')
                master_override = UPDATED_MASTER
            if ((load_scene and digest(selected_stage / 'StageEntity.json') != manifest['stage_bundle']['entity_sha256']) or
                    (load_roster and digest(selected_stage / 'NpcExpected.tsv') != manifest['npc_expectations']['sha256'])):
                raise ValueError('Prepared stage files differ from the stage manifest')
        floors = manifest.get('invasion_floors', {})
        if floors and not invasion_level:
            invasion_level = min(map(int, floors))
        floor = floors.get(str(invasion_level)) if invasion_level else None
        if invasion_level and floor is None:
            raise ValueError(f'Stage {stage_id} has no invasion level {invasion_level}')
        invasion_id = floor['id'] if floor else 0
        if load_player:
            if formation is not None:
                from formation import write_player_inputs

                player_source = directory / 'formation'
                write_player_inputs(player_source, formation, stage_id, master_override or stage / 'master_database.bytes',
                                    saved_supporters=saved_supporters)
            if invasion_id:
                player_database = invasion_player_database(player_source, directory, stage_id, invasion_id)
        resource = manifest['stage_bundle']['index']['name'].removeprefix('stage_')
        stage_expected = directory / 'StageExpected.tsv'
        stage_expected.write_text(
            f"{stage_id} {resource} {manifest['map_id']} {manifest['npc_count']} {manifest['map_chip_count']} "
            f"{manifest.get('initial_npcs', manifest['npc_count'])} {manifest.get('tower_event_stage_id', 0)} "
            f"{invasion_id} {floor['buff_rate'] if floor else 0} {manifest.get('gimmick_count', 0)} "
            f"{manifest.get('initial_gimmicks', 0)}\n", encoding='ascii')
    guest_mode = f'{mode}-trace' if trace_nuls else mode
    log = directory / f'{guest_mode}.log'
    inputs = {}
    if master_override:
        inputs['master_database.bytes'] = master_override
        if load_exchange and UPDATED_OCTO.is_file():
            inputs['OctoCatalog.bytes'] = UPDATED_OCTO
    if stage_expected is not None:
        inputs['StageExpected.tsv'] = stage_expected
    if load_scene:
        inputs['StageEntity.json'] = selected_stage / 'StageEntity.json'
    if load_roster:
        inputs['NpcExpected.tsv'] = selected_stage / 'NpcExpected.tsv'
    if load_player:
        inputs['PlayerDatabase.bytes'] = player_database or player_source / 'PlayerDatabase.bytes'
        for name in ('PlayerSelection.tsv', 'PlayerExpected.tsv'):
            inputs[name] = player_source / name
        if formation is not None:
            inputs['CustomFormation.enabled'] = player_source / 'CustomFormation.enabled'
    return root, log, pinned, verified_files, inputs


def timeline_inputs(directory, *, timeline_ui=False, resume=None):
    inputs = {}
    if timeline_ui:
        marker = directory / 'BattleTimeline.enabled'
        marker.touch()
        inputs[marker.name] = marker
    if resume is not None:
        inputs['BattleTimeline.bin'] = resume
    return inputs


def prepare_guest(mode, stage_id, emulator=BIN / EXECUTABLE, trace_nuls=False, *, invasion_level=0,
                  progress=None, timeline_ui=False, resume=None, formation=None):
    emulator = emulator.resolve()
    if not emulator.is_file():
        raise ValueError(f'QEMU executable not found: {emulator}')
    root, log, pinned, verified_files, inputs = prepare_battle_files(
        mode, stage_id, trace_nuls, invasion_level=invasion_level, formation=formation)
    inputs.update(timeline_inputs(log.parent, timeline_ui=timeline_ui, resume=resume))
    if progress:
        progress(2, '编译战斗入口并准备共用运行文件')
    with tempfile.TemporaryDirectory(prefix='build-', dir=KERNEL) as scratch:
        build_directory = Path(scratch)
        executable = build_directory / 'battle_reuse'
        build(root, executable)
        files = initramfs_files(root, build_init(build_directory), executable)
        for name in ('master_database.bytes', 'OctoCatalog.bytes'):
            if name in inputs:
                files[f'game/stage/{name}'] = inputs.pop(name)
        ramfs = cached_initramfs(files, verified_files)
    directory = log.parent
    guest_mode = log.stem
    command = [str(emulator), '-machine', 'virt', '-cpu', 'cortex-a72',
               '-accel', 'tcg,thread=multi', '-smp', '2', '-m', '1536',
               '-kernel', str(KERNEL / 'Image'), '-initrd', os.path.relpath(ramfs, directory),
               '-nodefaults', '-nographic', '-serial', 'stdio', '-monitor', 'none',
               '-nic', 'none', '-no-reboot', '-append', f'console=ttyAMA0 loglevel=4 panic=-1 -- {guest_mode}']
    for name, source in inputs.items():
        path = os.path.relpath(source, directory).replace(',', ',,')
        command.extend(('-fw_cfg', f'name=opt/battle/{name},file={path}'))
    return command, log, pinned


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('link', 'init', 'stage', 'scene', 'roster', 'deployment', 'opening', 'strikes',
                                         'exchange', 'phase', 'enemy-phase', 'stage-run', 'interactive'))
    parser.add_argument('--stage', type=int, default=STAGE, help='original battle stage ID')
    parser.add_argument('--invasion-level', type=int, default=0, help='Strong Enemy extension level (1-20)')
    parser.add_argument('--qemu', type=Path, default=BIN / EXECUTABLE,
                        help='path to a local qemu-system-aarch64 executable')
    parser.add_argument('--timeout', type=int, help='guest timeout in seconds for unattended modes')
    parser.add_argument('--trace-nulls', action='store_true', help='report native exception throw sites')
    args = parser.parse_args()
    try:
        if args.stage != STAGE and args.mode not in ('link', 'init') and not stage_prepared(args.stage):
            extract_additional_stage(args.stage, fetch=True, dependencies=False)
        command, log, pinned = prepare_guest(args.mode, args.stage, args.qemu, args.trace_nulls,
                                             invasion_level=args.invasion_level)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    timeout_seconds = args.timeout if args.timeout is not None else (600 if args.mode == 'stage-run' else 150)
    timeout = False
    try:
        with log.open('wb') as output:
            if args.mode == 'interactive':
                with subprocess.Popen(command, cwd=log.parent, stdout=subprocess.PIPE, stderr=subprocess.STDOUT) as guest:
                    for line in guest.stdout:
                        output.write(line)
                        output.flush()
                        sys.stdout.buffer.write(line)
                        sys.stdout.buffer.flush()
                    qemu_exit = guest.wait()
            else:
                result = subprocess.run(command, cwd=log.parent, stdin=subprocess.DEVNULL,
                                        stdout=output, stderr=subprocess.STDOUT, timeout=timeout_seconds)
                qemu_exit = result.returncode
    except subprocess.TimeoutExpired:
        timeout = True
        qemu_exit = None
    transcript = log.read_text('utf-8', errors='replace')
    exit_marker = re.search(r'^REUSE kernel_guest_exit (\d+)', transcript, re.M)
    guest_exit = int(exit_marker[1]) if exit_marker else None
    markers = [line for line in transcript.splitlines() if line.startswith('REUSE ')]
    report = {'qemu': command[0], 'qemu_exit': qemu_exit, 'guest_exit': guest_exit,
              'timed_out': timeout, 'original_sha256': pinned, 'markers': markers}
    log.with_suffix('.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, indent=2))
    if timeout or qemu_exit or guest_exit is None:
        print('\n'.join(transcript.splitlines()[-16:]))
        return 1
    return guest_exit


if __name__ == '__main__':
    raise SystemExit(main())
