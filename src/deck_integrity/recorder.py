"""Collects results and command transcripts; writes results.json as it goes.

results.json is rewritten after every recorded result, so a crash, power
loss or killed process still leaves the evidence gathered up to that point.
"""
from __future__ import annotations

import datetime
import json
import os
import pathlib
import time

from deck_integrity import VERSION

STATUSES = ('PASS', 'FAIL', 'WARN', 'ERROR', 'SKIP', 'INFO')


def utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')


class Recorder:
    def __init__(self, out_dir: pathlib.Path, redact, echo=print):
        self.out = out_dir
        self.out.mkdir(parents=True, exist_ok=True)
        self.redact = redact
        self.echo = echo
        self.report = self.out / 'report.txt'
        self.started = utc_now()
        self.finished = None
        self.state = 'running'   # running | completed | interrupted | crashed
        self.args: dict = {}
        self.system: dict = {}
        self.results: list = []
        self.commands: list = []

    # -- text log -------------------------------------------------------
    def log(self, msg='') -> None:
        s = self.redact(msg)
        if self.echo:
            self.echo(s, flush=True)
        with self.report.open('a', encoding='utf-8', errors='replace') as f:
            f.write(s + '\n')

    def section(self, title: str) -> None:
        self.log('\n=== ' + title + ' ===')

    # -- structured -----------------------------------------------------
    def command(self, name: str, result) -> dict:
        """Log a CommandResult/PipelineResult transcript and keep it for results.json."""
        d = self.redact_obj(result.to_dict())
        d['name'] = name
        d['at'] = utc_now()
        self.commands.append(d)
        return d

    def record(self, name: str, status: str, summary: str = '', data: dict | None = None,
               category: str = '', started: float | None = None) -> dict:
        assert status in STATUSES, status
        entry = {'test': name, 'category': category, 'status': status,
                 'summary': self.redact(summary), 'at': utc_now(),
                 'elapsed_s': round(time.monotonic() - started, 3) if started else None,
                 'data': self.redact_obj(data or {})}
        self.results.append(entry)
        self.log(f'[{status}] {name}: {entry["summary"]}')
        self.save()
        return entry

    def redact_obj(self, obj):
        if isinstance(obj, str):
            return self.redact(obj)
        if isinstance(obj, dict):
            return {k: self.redact_obj(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [self.redact_obj(v) for v in obj]
        return obj

    def counts(self) -> dict:
        return {s: sum(r['status'] == s for r in self.results) for s in STATUSES}

    def to_dict(self) -> dict:
        return {'tool': 'Deck Integrity Suite', 'version': VERSION, 'state': self.state,
                'started_utc': self.started, 'finished_utc': self.finished,
                'args': self.args, 'counts': self.counts(), 'tests': self.results,
                'commands': self.commands}

    def save(self) -> None:
        write_json(self.out / 'results.json', self.to_dict())


def write_json(path: pathlib.Path, obj) -> None:
    """Atomic write: never leave a half-written JSON file behind."""
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(obj, indent=2, default=str), encoding='utf-8')
    os.replace(tmp, path)
