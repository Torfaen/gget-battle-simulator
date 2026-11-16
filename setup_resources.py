"""Download public setup archives and install bundled unit illustrations."""

import argparse
import json
from pathlib import Path, PurePosixPath
import re
from shutil import copyfileobj
from urllib.request import urlopen
from zipfile import ZipFile


def download_archive(url, target):
    temporary = target.with_suffix('.part')
    try:
        with urlopen(url, timeout=45) as response, temporary.open('wb') as output:
            copyfileobj(response, output, length=1024 * 1024)
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
    args = parser.parse_args()
    if args.action == 'download':
        download_archive(args.source, args.destination)
    else:
        install_portraits(Path(args.source), args.destination)


if __name__ == '__main__':
    main()
