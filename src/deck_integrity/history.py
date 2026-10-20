"""Past runs: read the summary of every report folder for comparison."""
from __future__ import annotations

import json
import pathlib

REPORTS_DIR = pathlib.Path.home() / 'deck-integrity-reports'


def verdict(state: str | None, counts: dict) -> str:
    """One-word outcome: faults, errors, stopped, clean or unknown."""
    counts = counts or {}
    if counts.get('FAIL'):
        return 'faults'
    if state in ('interrupted', 'crashed'):
        return 'stopped'
    if counts.get('ERROR'):
        return 'errors'
    if state == 'completed':
        return 'clean'
    return 'unknown'


def load_runs(root: pathlib.Path = REPORTS_DIR, limit: int = 200) -> list:
    """Newest first. Folders without a readable results.json are listed as unknown."""
    if not root.is_dir():
        return []
    runs = []
    for d in sorted((p for p in root.iterdir() if p.is_dir()), reverse=True)[:limit]:
        info = {'name': d.name, 'folder': str(d), 'state': None, 'counts': {}, 'mode': None,
                'started_utc': None, 'version': None}
        try:
            r = json.loads((d / 'results.json').read_text(encoding='utf-8'))
            info.update(state=r.get('state'), counts=r.get('counts') or {},
                        mode=(r.get('args') or {}).get('mode'), started_utc=r.get('started_utc'),
                        version=r.get('version'),
                        first_failure=((r.get('run') or {}).get('scheduler') or {}).get('first_failure'))
        except (OSError, ValueError):
            pass
        info['verdict'] = verdict(info['state'], info['counts'])
        runs.append(info)
    return runs
