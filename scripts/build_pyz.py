"""Build dist/deck-integrity.pyz: a single file that runs with the system python3.

Usage:  python scripts/build_pyz.py
On the device:  python3 deck-integrity.pyz --quick
"""
import pathlib
import shutil
import tempfile
import zipapp

ROOT = pathlib.Path(__file__).resolve().parent.parent
DIST = ROOT / 'dist'


def main() -> None:
    DIST.mkdir(exist_ok=True)
    target = DIST / 'deck-integrity.pyz'
    with tempfile.TemporaryDirectory() as tmp:
        stage = pathlib.Path(tmp)
        shutil.copytree(ROOT / 'src' / 'deck_integrity', stage / 'deck_integrity',
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        zipapp.create_archive(stage, target, interpreter='/usr/bin/env python3',
                              main='deck_integrity.cli:main_exit')
    print(f'Built {target} ({target.stat().st_size // 1024} KiB)')


if __name__ == '__main__':
    main()
