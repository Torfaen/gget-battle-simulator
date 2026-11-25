"""Build the artwork update only after the combined release passes its coverage check."""

import argparse
import json
from pathlib import Path
from zipfile import ZIP_STORED, ZipFile

from check_release_art import check_release
from original_battle_reuse.koma_assets import is_unused_koma_direction
from update_release_art import ART_PATH, digest, read_descriptor


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--archive', type=Path, action='append', required=True)
    parser.add_argument('--art-dir', type=Path, required=True)
    parser.add_argument('--tag', required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    report = check_release(args.runtime, args.archive, args.art_dir, write_index=True)
    (args.out / 'coverage-report.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    if any(report['missing_or_invalid'].values()) or report['incorrect_index']:
        raise SystemExit('Artwork coverage failed. See coverage-report.json.')

    groups = {'battle_ui_assets/pilot_portraits/': 'battle-pilot-art-delta.zip',
              'battle_ui_assets/supporter_portraits/': 'battle-supporter-art-delta.zip',
              'idle_koma/': 'battle-koma-art-delta.zip'}
    files = {path.relative_to(args.art_dir).as_posix(): path for path in args.art_dir.rglob('*') if path.is_file()}
    if any(not ART_PATH.fullmatch(relative) for relative in files):
        raise SystemExit('Unexpected file in the artwork staging directory.')

    directional = set(report['directional_koma'])
    packs = []
    for prefix, name in groups.items():
        selected = {relative: path for relative, path in sorted(files.items())
                    if relative.startswith(prefix) and not is_unused_koma_direction(relative, directional)}
        if not selected:
            continue
        archive_path = args.out / name
        with ZipFile(archive_path, 'w', compression=ZIP_STORED) as archive:
            for relative, path in selected.items():
                archive.write(path, relative)
        packs.append({'asset': name, 'sha256': digest(archive_path),
                      'files': {relative: digest(path) for relative, path in selected.items()}})
        print(f'{name}: {len(selected)} files, {archive_path.stat().st_size:,} bytes')

    descriptor = json.dumps({'schema': 1, 'tag': args.tag, 'packs': packs}, indent=2) + '\n'
    read_descriptor(descriptor)
    (args.out / 'art-update.json').write_text(descriptor, encoding='utf-8')


if __name__ == '__main__':
    main()
