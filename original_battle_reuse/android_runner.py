"""Run the extracted library in an ARM64 Android user process on Windows."""

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import struct
import subprocess
import sys

from prepare_android import ANDROID, ARTIFACTS, HERE


def build(root, output=None, *, android_bridge=False):
    compiler = ARTIFACTS / 'zig_env/ziglang/zig.exe'
    if not compiler.is_file():
        raise FileNotFoundError('Install ziglang==0.15.2 into artifacts/zig_env')
    environment = os.environ.copy()
    environment['ZIG_GLOBAL_CACHE_DIR'] = str(ANDROID / 'zig_cache')
    environment['ZIG_LOCAL_CACHE_DIR'] = str(ANDROID / 'zig_local')
    subprocess.run([
        str(compiler), 'cc', '-target', 'aarch64-linux-none', '-nostdlib', '-ffreestanding', '-fno-stack-protector',
        '-fPIE', '-pie', '-Wl,-e,_start', '-Wl,--dynamic-linker=/system/bin/linker64',
        *(['-DANDROID_BRIDGE'] if android_bridge else []),
        str(HERE / 'android_entry.c'), str(HERE / 'android_unity.c'), str(HERE / 'android_roster.c'),
        str(HERE / 'android_trace.c'), str(HERE / 'android_opening.c'), str(HERE / 'android_combat.c'),
        str(HERE / 'android_exchange.c'), str(HERE / 'android_octo.c'), str(HERE / 'android_enemy.c'),
        str(HERE / 'android_interactive.c'), str(HERE / 'android_stage_events.c'),
        str(HERE / 'android_snapshot.c'),
        str(root / 'system/lib64/libc.so'), str(root / 'system/lib64/libdl.so'),
        '-o', str(output if output is not None else root / 'bin/battle_reuse'),
    ], env=environment, cwd=ANDROID, check=True)


def run(root, mode, verbose, metadata, no_gc, seconds):
    sys.path.insert(0, str(ARTIFACTS / 'qiling_env'))
    from gevent import Timeout
    from qiling import Qiling
    from qiling.const import QL_INTERCEPT, QL_VERBOSE
    from qiling.exception import QlMemoryMappedError, QlOutOfMemory
    from qiling.extensions.pipe import SimpleOutStream
    from qiling.os.linux.procfs import FsMappedStream
    from qiling.os.posix.syscall.mman import ql_syscall_mmap, ql_syscall_munmap

    executable = 'bin/arm64_android_hello' if mode == 'hello' else 'bin/battle_reuse'
    # A relative ASCII argv[0] avoids Qiling's latin-1 guest-stack encoder.
    previous = Path.cwd()
    os.chdir(root)
    exit_status = []
    opened = []
    large_mmaps = []
    mmap_counts = Counter()
    signals = []
    alternate_stacks = {}
    timed_out = False
    timeout_pc = None
    output = SimpleOutStream(1)
    errors = SimpleOutStream(2)
    environment = {'LD_LIBRARY_PATH': '/game:/system/lib64'}
    if no_gc:
        environment['GC_DONT_GC'] = '1'
    try:
        def on_exit(_ql, code):
            exit_status.append(code)

        def on_open(ql, _directory, path, _flags, _mode, result):
            opened.append({'path': ql.os.utils.read_cstring(path), 'result': result})

        def no_socket(_ql, _domain, _type, _protocol):
            return -101  # Linux ENETUNREACH

        def munmap(ql, address, length):
            # Qiling 1.4.6 sends zero-length unmaps to Unicorn's mapping table.
            if not length or address % ql.mem.pagesize:
                return -22  # Linux EINVAL
            return ql_syscall_munmap(ql, address, length)

        def mmap(ql, address, length, protection, flags, fd, offset):
            mmap_counts['total'] += 1
            if length >= 32 * 1024 * 1024:
                large_mmaps.append(dict(address=hex(address), size=length, flags=hex(flags),
                                        fd=fd, offset=offset, pc=hex(ql.arch.regs.arch_pc)))
            # A malformed allocation must stay within the test process's memory budget.
            if length > 512 * 1024 * 1024:
                return -12  # Linux ENOMEM
            if flags in (0x22, 0x32, 0x4022) and not offset:
                # Private anonymous mappings are already zero-filled by Unicorn.
                # Avoid allocating and copying a second zero buffer in Qiling.
                if not length or protection & ~7:
                    return -22
                size = ql.mem.align_up(length)
                try:
                    if flags & 0x10:  # MAP_FIXED
                        if address % ql.mem.pagesize:
                            return -22
                        ql.mem.unmap_between(address, address + size)
                    else:
                        address = ql.mem.find_free_space(size, max(address, ql.loader.mmap_address))
                    ql.mem.map(address, size, protection, info='[mmap anonymous]')
                except (QlMemoryMappedError, QlOutOfMemory):
                    return -12
                mmap_counts['anonymous'] += 1
                return address
            return ql_syscall_mmap(ql, address, length, protection, flags, fd, offset)

        def sigaltstack(ql, new, previous):
            thread = ql.os.thread_management.cur_thread.id
            old = alternate_stacks.get(thread, struct.pack('<Qi4xQ', 0, 2, 0))
            if new:
                updated = bytes(ql.mem.read(new, 24))
                _, flags, size = struct.unpack('<Qi4xQ', updated)
                if flags not in (0, 2):
                    return -22
                if not flags and size < 5120:
                    return -12
                alternate_stacks[thread] = updated
            if previous:
                ql.mem.write(previous, old)
            return 0

        def tgkill(ql, process, thread, signal):
            signals.append(dict(process=process, thread=thread, signal=signal,
                                pc=hex(ql.arch.regs.arch_pc), caller=hex(ql.arch.regs.x30)))
            return -38  # Linux ENOSYS

        ql = Qiling([executable, mode], str(root), env=environment,
                    multithread=True, verbose=QL_VERBOSE.DEFAULT if verbose else QL_VERBOSE.OFF)
        ql.os.stdout = output
        ql.os.stderr = errors
        ql.os.set_syscall('exit_group', on_exit, QL_INTERCEPT.ENTER)
        ql.os.set_syscall('openat', on_open, QL_INTERCEPT.EXIT)
        ql.os.set_syscall('socket', no_socket)
        ql.os.set_syscall('mmap', mmap)
        ql.os.set_syscall('munmap', munmap)
        ql.os.set_syscall('sigaltstack', sigaltstack)
        ql.os.set_syscall('tgkill', tgkill)

        def process_stat():
            fields = ['0'] * 52
            fields[0:3] = [str(ql.os.profile.getint('KERNEL', 'pid')), '(battle_reuse)', 'R']
            fields[19] = str(max(1, len(ql.os.thread_management.threads)))
            fields[27] = str(ql.loader.stack_address)  # startstack, from the emulated ELF loader
            return FsMappedStream('/proc/self/stat', (' '.join(fields) + '\n').encode('ascii'))

        def process_maps():
            lines = []
            for start, end, permissions, label, host_path in ql.mem.get_mapinfo():
                name = label
                if host_path:
                    relative = Path(host_path).resolve().relative_to(root)
                    name = '/' + relative.as_posix()
                lines.append(f'{start:x}-{end:x} {permissions}p 00000000 00:00 0 {name}\n')
            return FsMappedStream('/proc/self/maps', ''.join(lines).encode('utf-8'))

        ql.add_fs_mapper('/proc/self/stat', process_stat)
        ql.add_fs_mapper('/proc/self/maps', process_maps)
        if metadata == 'packaged':
            ql.add_fs_mapper('/game/Data/Managed/Metadata/global-metadata.dat',
                             str(root / 'game/global-metadata.packaged.dat'))
        with Timeout(seconds) as timer:
            try:
                ql.run()
            except Timeout as exc:
                if exc is not timer:
                    raise
                timed_out = True
                timeout_pc = hex(ql.arch.regs.arch_pc)
                print(f'Guest timed out after {seconds}s at PC={timeout_pc}', file=sys.stderr)
                return 124
        if not exit_status:
            raise RuntimeError(f'Guest stopped before exit_group, PC={ql.arch.regs.arch_pc:#x}')
        return exit_status[-1]
    finally:
        os.chdir(previous)
        stdout = output.getvalue().decode('utf-8', errors='replace')
        stderr = errors.getvalue().decode('utf-8', errors='replace')
        report = {'mode': mode, 'metadata': metadata, 'gc_disabled': no_gc,
                  'exit_code': exit_status[-1] if exit_status else None,
                  'timed_out': timed_out, 'timeout_pc': timeout_pc,
                  'stdout': stdout, 'stderr': stderr, 'openat': opened, 'large_mmaps': large_mmaps,
                  'signals': signals, 'mmap_counts': mmap_counts}
        suffix = ('_packaged' if metadata == 'packaged' else '') + ('_no_gc' if no_gc else '')
        (ANDROID / f'{mode}{suffix}.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
        print(stdout, end='')
        print(stderr, end='', file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('hello', 'link', 'init'))
    parser.add_argument('--verbose', action='store_true')
    parser.add_argument('--metadata', choices=('decoded', 'packaged'), default='packaged')
    parser.add_argument('--no-gc', action='store_true', help='diagnose initialization without GC signal delivery')
    parser.add_argument('--timeout', type=int, default=45, choices=range(1, 601), metavar='SECONDS')
    args = parser.parse_args()
    root = ANDROID / 'rootfs'
    if args.mode != 'hello':
        build(root)
    return run(root, args.mode, args.verbose, args.metadata, args.no_gc, args.timeout)


if __name__ == '__main__':
    raise SystemExit(main())
