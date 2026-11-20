"""Show setup downloads on stderr and write diagnostic messages to the setup log."""

from datetime import datetime
from http.client import HTTPException
import os
from pathlib import Path
import sys
from threading import Lock
from time import monotonic
from urllib.request import urlopen


class DownloadError(OSError):
    """A failed transfer with the name of the requested file."""


_output_lock = Lock()


def log_message(message, log_path=None):
    path = log_path or os.environ.get('GGET_SETUP_LOG')
    if path:
        with _output_lock, Path(path).open('a', encoding='utf-8') as log:
            log.write(f'[{datetime.now().isoformat(timespec="seconds")}] {message}\n')


def report_problem(message):
    print(message, file=sys.stderr, flush=True)
    try:
        log_message(message)
    except OSError as exc:
        print(f'Could not write the setup log: {exc}', file=sys.stderr, flush=True)


def byte_size(size):
    if size >= 1024 * 1024:
        return f'{size / (1024 * 1024):.1f} MiB'
    if size >= 1024:
        return f'{size / 1024:.1f} KiB'
    return f'{int(size)} B'


def progress_text(received, total, elapsed, *, finished=False):
    downloaded = byte_size(received)
    speed = f'  {byte_size(received / elapsed)}/s' if received and elapsed > 0 else ''
    if total:
        percent = 100 if finished else min(99, 100 * received // total)
        filled = percent // 5
        bar = '#' * filled + '-' * (20 - filled)
        return f'[{bar}] {percent:3d}%  {downloaded} / {byte_size(total)}{speed}'
    status = 'Complete' if finished else 'Downloaded'
    return f'{status} {downloaded}{speed}'


def download_to_file(url, target, name, *, timeout, inline=True):
    """Download into a caller-owned temporary file, leaving verification to the caller."""
    stream = sys.stderr
    inline = inline and stream.isatty()
    width = 0

    def show(status, *, finished=False):
        nonlocal width
        line = status if inline else f'{name}  {status}'
        with _output_lock:
            stream.write(('\r' if inline else '') + line.ljust(width) + ('\n' if finished or not inline else ''))
            stream.flush()
        width = len(line) if inline else 0

    if inline:
        print(name, file=stream, flush=True)
    show('Connecting...')
    try:
        with urlopen(url, timeout=timeout) as response, target.open('wb') as output:
            length = response.headers.get('Content-Length', '')
            total = int(length) if length.isascii() and length.isdecimal() else None
            count = 0
            started = last_update = monotonic()
            if inline:
                show(progress_text(0, total, 0))
            while block := response.read1(256 * 1024):
                output.write(block)
                count += len(block)
                now = monotonic()
                if now - last_update >= 1 and (not total or count < total):
                    show(progress_text(count, total, now - started))
                    last_update = now
            if total and count != total:
                raise OSError(f'Incomplete download ({byte_size(count)} received, expected {byte_size(total)})')
        show(progress_text(count, total, monotonic() - started, finished=True), finished=True)
    except (OSError, HTTPException, ValueError) as exc:
        if inline:
            stream.write('\n')
            stream.flush()
        reason = str(exc) or type(exc).__name__
        raise DownloadError(f'{name}: {reason}') from exc
