"""Download public setup archives and install bundled unit illustrations."""

import argparse
import json
from pathlib import Path, PurePosixPath
import re
from shutil import copyfileobj
from zipfile import BadZipFile, ZipFile

from original_battle_reuse.download_progress import download_to_file, report_problem
from startup_update import matches


def download_archive(url, target, checksum=None):
    temporary = target.with_suffix('.part')
    try:
        download_to_file(url, temporary, target.name, timeout=45)
        if checksum and not matches(temporary, checksum):
            raise ValueError(f'SHA-256 mismatch: {target.name}')
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def install_portraits(source, destination):
    with ZipFile(source) as archive:
        index = 'outputs/latest_mech_assets.json'
        run = json.loads(archive.read(index))['run_id']
        expected_parent = PurePosixPath('outputs', 'updates', run, 'portraits')
        entries = [PurePosixPath(name) for name in archive.namelist() if name != index]
        if not entries or any(path.parent != expected_parent or
                              not re.fullmatch(r'ub_[A-Za-z0-9_-]+\.png', path.name) for path in entries):
            raise ValueError('Unexpected entry in the unit portrait archive')
        destination.mkdir(parents=True, exist_ok=True)
        for path in entries:
            with archive.open(path.as_posix()) as image, (destination / path.name).open('wb') as output:
                copyfileobj(image, output)
        print(f'Installed {len(entries)} unit portraits')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('download', 'portraits'))
    parser.add_argument('source')
    parser.add_argument('destination', type=Path)
    parser.add_argument('--sha256', help='expected SHA-256 of the downloaded archive')
    args = parser.parse_args()
    try:
        if args.action == 'download':
            download_archive(args.source, args.destination, args.sha256)
        else:
            install_portraits(Path(args.source), args.destination)
    except (OSError, ValueError, KeyError, BadZipFile) as exc:
        if args.action == 'portraits':
            report_problem(f'Extracting {Path(args.source).name} failed: {exc}')
        else:
            report_problem(f'Download failed: {exc}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
