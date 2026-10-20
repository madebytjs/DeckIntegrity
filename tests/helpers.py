"""Fakes for exercising checks without Linux tools or multi-GiB data."""
from __future__ import annotations

import argparse
import hashlib
import pathlib
import re

from deck_integrity import hashing
from deck_integrity.checks import zero
from deck_integrity.checks.common import Context
from deck_integrity.recorder import Recorder
from deck_integrity.runner import CommandResult, PipelineResult

WRONG = 'f' * 64


def sha_file(path, flip_at=None) -> tuple:
    data = bytearray(pathlib.Path(path).read_bytes())
    if flip_at is not None:
        data[flip_at] ^= 0xFF
    return hashlib.sha256(data).hexdigest(), len(data)


class FakeTools:
    """Stands in for run_command/run_pipeline.

    `faults` maps a tool label ('head', 'dd', 'sha256sum', 'dd-direct') to a
    callable(call_number) returning one of: None (behave), 'wrong', 'flip',
    'timeout', 'exit', 'missing', or KeyboardInterrupt to raise.
    """

    def __init__(self, faults=None):
        self.faults = faults or {}
        self.calls = {}

    def _fault(self, label):
        n = self.calls[label] = self.calls.get(label, 0) + 1
        f = self.faults.get(label)
        out = f(n) if f else None
        if out is KeyboardInterrupt:
            raise KeyboardInterrupt
        return out

    def _sha_line(self, sha):
        return f'{sha}  -\n'

    def run_command(self, argv, timeout):
        label = argv[0]
        fault = self._fault(label)
        r = CommandResult(argv=argv, elapsed=0.01)
        if fault == 'missing':
            r.error = 'not installed: ' + argv[0]
            return r
        if fault == 'timeout':
            r.timed_out = True
            r.returncode = -9
            return r
        sha, _ = sha_file(argv[-1], flip_at=0 if fault == 'flip' else None)
        r.returncode = 1 if fault == 'exit' else 0
        r.stdout = self._sha_line(WRONG if fault == 'wrong' else sha)
        return r

    def run_pipeline(self, producer, consumer, timeout):
        src = next((a[3:] for a in producer if a.startswith('if=')), producer[-1])
        label = producer[0] + ('-direct' if 'iflag=direct' in producer else '')
        fault = self._fault(label)
        p = CommandResult(argv=producer, returncode=0, elapsed=0.01)
        c = CommandResult(argv=consumer, returncode=0, elapsed=0.01)
        res = PipelineResult(p, c, elapsed=0.01)
        if fault == 'missing':
            p.error = 'not installed: ' + producer[0]
            p.returncode = c.returncode = None
            return res
        if fault == 'timeout':
            c.timed_out = True
            return res
        if pathlib.Path(src).name == 'zero':   # the check's zero device
            sha, n = zero.EXPECTED_2GIB, zero.SIZE
        else:
            sha, n = sha_file(src, flip_at=0 if fault == 'flip' else None)
        if fault == 'wrong':
            sha = WRONG
        if fault == 'short':
            n -= 4096
        if fault == 'exit':
            p.returncode = 1
        if producer[0] == 'dd':
            p.stderr = f'{n // 4194304}+0 records in\n{n // 4194304}+0 records out\n{n} bytes copied\n'
        c.stdout = self._sha_line(sha)
        return res


def make_args(**kw):
    base = dict(passes=3, file_mib=32, ram_mib=32, keep_file=False, skip=[], quick=False, output=None)
    base.update(kw)
    return argparse.Namespace(**base)


def make_ctx(tmp: pathlib.Path, tools: FakeTools | None = None, **kw) -> Context:
    tools = tools or FakeTools()
    rec = Recorder(tmp / 'out', redact=lambda s: str(s), echo=None)
    ctx = Context(rec=rec, args=kw.pop('args', make_args()), home=tmp, shm=tmp / 'shm',
                  zero_device=str(tmp / 'zero'),
                  run_command=tools.run_command, run_pipeline=tools.run_pipeline,
                  snapshot=lambda: {'uptime_s': 100.0, 'temps_c': {'cpu/Tctl': 55.0}})
    (tmp / 'shm').mkdir(exist_ok=True)
    (tmp / 'zero').write_bytes(b'')   # existence check only; reads are faked
    ctx.hash_device_stream = lambda path, total: hashing.HashResult(zero.EXPECTED_2GIB, total, 0.01)
    for k, v in kw.items():
        setattr(ctx, k, v)
    return ctx


def by_test(rec, pattern) -> list:
    return [r for r in rec.results if re.search(pattern, r['test'])]
