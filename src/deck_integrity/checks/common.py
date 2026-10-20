"""Shared machinery for integrity checks.

A check runs several independent *methods* (e.g. coreutils sha256sum vs
Python hashlib, buffered vs direct I/O) over the same data. Methods are
interleaved pass by pass, so a fault that comes and goes over time shows up
across methods instead of all landing on whichever ran last.
"""
from __future__ import annotations

import os
import pathlib
import re
import shutil
import time
from dataclasses import dataclass, field
from typing import Callable

from deck_integrity import hashing, runner, sensors

# GNU sha256sum prefixes the line with '\' when the file name needs escaping.
SHA_RE = re.compile(r'^\\?([0-9a-f]{64})\b')
DD_BYTES_RE = re.compile(r'^(\d+) bytes', re.M)
MAX_CHUNK_REPORT = 64


@dataclass
class Context:
    rec: object
    args: object
    home: pathlib.Path = field(default_factory=pathlib.Path.home)
    shm: pathlib.Path = pathlib.Path('/dev/shm')
    zero_device: str = '/dev/zero'
    run_command: Callable = runner.run_command
    run_pipeline: Callable = runner.run_pipeline
    hash_file: Callable = hashing.hash_file
    hash_device_stream: Callable = hashing.hash_device_stream
    generate_random_file: Callable = hashing.generate_random_file
    evict_cache: Callable = hashing.evict_cache
    snapshot: Callable = sensors.snapshot

    @staticmethod
    def timeout_for(size_mib: int) -> float:
        """Generous per-operation deadline: 2 minutes plus 20 MiB/s worst case."""
        return 120 + size_mib / 20


@dataclass
class Method:
    name: str
    implementation: str        # 'coreutils' or 'python'
    fn: Callable[[int], dict]  # pass number -> observation dict
    unsupported: str = ''
    observations: list = field(default_factory=list)


# -- observation builders ---------------------------------------------------

def tool_obs(ctx: Context, label: str, result, dd_bytes: bool = False) -> dict:
    """Turn a CommandResult/PipelineResult whose last stage is sha256sum into an observation."""
    ctx.rec.command(label, result)
    consumer = getattr(result, 'consumer', result)
    producer = getattr(result, 'producer', None)
    obs = {'sha256': None, 'bytes': None, 'elapsed_s': round(result.elapsed, 3), 'error': ''}
    if result.missing:
        obs['unsupported'] = (producer.error if producer and producer.error else consumer.error)
        return obs
    if producer is not None and dd_bytes:
        m = DD_BYTES_RE.search(producer.stderr)
        obs['bytes'] = int(m.group(1)) if m else None
    if result.timed_out:
        obs['error'] = f'timed out after {result.elapsed:.0f}s'
    elif not result.ok:
        codes = f'exit={consumer.returncode}' if producer is None else \
            f'exit producer={producer.returncode} sha256sum={consumer.returncode}'
        err = ((producer.stderr if producer else '') + ' ' + consumer.stderr + ' ' +
               getattr(result, 'error', '') + ' ' + consumer.error).strip()
        obs['error'] = f'{codes}; {err[:300]}'
    m = SHA_RE.match(consumer.stdout.strip())
    if m:
        obs['sha256'] = m.group(1)
    elif not obs['error']:
        obs['error'] = 'unparseable sha256sum output: ' + consumer.stdout[:120]
    return obs


def hash_obs(res: hashing.HashResult, reference_chunks: list | None = None) -> dict:
    obs = {'sha256': res.sha256 or None, 'bytes': res.bytes, 'elapsed_s': round(res.elapsed, 3),
           'error': '' if res.unsupported else res.error}
    if res.unsupported:
        obs['unsupported'] = res.error
    if reference_chunks and res.ok and res.chunk_hashes:
        diffs = hashing.diff_chunks(reference_chunks, res.chunk_hashes)
        if diffs:
            obs['chunk_mismatch_count'] = len(diffs)
            obs['chunk_mismatches'] = diffs[:MAX_CHUNK_REPORT]
    return obs


# -- evaluation -------------------------------------------------------------

def evaluate(observations: list, expected_sha: str | None, expected_bytes: int | None) -> tuple:
    """Return (status, summary, stats) for one method's passes."""
    done = [o for o in observations if not o.get('error') and not o.get('unsupported')]
    errored = [o for o in observations if o.get('error')]
    hashes = [o['sha256'] for o in done]
    distinct = sorted(set(hashes))
    if expected_sha:
        wrong = [o for o in done if o['sha256'] != expected_sha]
    else:  # no reference: the majority hash is the best guess, any deviation is a fault
        common = max(distinct, key=hashes.count) if distinct else None
        wrong = [o for o in done if o['sha256'] != common]
    short = [o for o in done if expected_bytes and o.get('bytes') is not None
             and o['bytes'] != expected_bytes]
    stats = {'passes_attempted': len(observations), 'passes_completed': len(done),
             'passes_wrong_hash': len(wrong), 'passes_wrong_byte_count': len(short),
             'passes_errored': len(errored), 'distinct_hashes': len(distinct)}
    if not observations:
        return 'SKIP', 'no passes run', stats
    if wrong or short:
        parts = []
        if wrong:
            kind = ('consistent across passes but different from the expected value'
                    if len(distinct) == 1 else f'inconsistent: {len(distinct)} distinct hashes')
            parts.append(f'{len(wrong)} of {len(done)} completed passes gave an incorrect hash ({kind})')
        if short:
            parts.append(f'{len(short)} passes processed the wrong number of bytes')
        return 'FAIL', '; '.join(parts), stats
    if not done:
        first = errored[0]['error'] if errored else 'unknown'
        return 'ERROR', f'no pass completed ({len(errored)} errors); first: {first}', stats
    tail = f'; sha256={distinct[0]}'
    if errored:
        return 'WARN', (f'{len(done)} completed passes all correct, but {len(errored)} '
                        f'could not complete: {errored[0]["error"]}'), stats
    return 'PASS', f'{len(done)}/{len(done)} passes correct{tail}', stats


def run_methods(ctx: Context, check: str, methods: list, passes: int,
                expected_sha: str | None, expected_bytes: int | None, meaning: str,
                extra: dict | None = None) -> None:
    """Run methods round-robin for `passes` passes, then record one result per method.

    Results are recorded in a finally block, so an interrupted run still
    reports every pass that finished.
    """
    rec = ctx.rec
    before = ctx.snapshot()
    t0 = time.monotonic()
    first_failure_logged = False
    interrupted = False
    try:
        for p in range(1, passes + 1):
            for m in methods:
                if m.unsupported:
                    continue
                obs = m.fn(p)
                obs['pass'] = p
                snap = ctx.snapshot() or {}
                temps = snap.get('temps_c') or {}
                obs['uptime_s'] = snap.get('uptime_s')
                obs['max_temp_c'] = max(temps.values()) if temps else None
                if obs.get('unsupported'):
                    m.unsupported = obs['unsupported']
                    rec.log(f'  {m.name}: not available ({m.unsupported})')
                    continue
                m.observations.append(obs)
                bad = (obs.get('error') or (expected_sha and obs['sha256'] != expected_sha) or
                       (expected_bytes and obs.get('bytes') is not None and obs['bytes'] != expected_bytes))
                flag = '' if not bad else ('  <-- ERROR' if obs.get('error') else '  <-- MISMATCH')
                nbytes = f'{obs["bytes"]} bytes, ' if obs.get('bytes') is not None else ''
                rec.log(f'  {m.name} pass {p}/{passes}: {obs["sha256"] or "-"} '
                        f'({nbytes}{obs["elapsed_s"]}s){flag}' +
                        (f' {obs["error"]}' if obs.get('error') else ''))
                if obs.get('chunk_mismatches'):
                    offs = ', '.join(str(c['offset_mib']) for c in obs['chunk_mismatches'][:8])
                    rec.log(f'    differing 32 MiB regions at MiB offsets: {offs}'
                            f'{" ..." if obs["chunk_mismatch_count"] > 8 else ""}')
                if bad and not obs.get('error') and not first_failure_logged:
                    first_failure_logged = True
                    rec.log(f'  FIRST FAILURE: {check} / {m.name}, pass {p}, '
                            f'{time.monotonic() - t0:.1f}s into this check')
    except KeyboardInterrupt:
        interrupted = True
        raise
    finally:
        after = ctx.snapshot()
        for m in methods:
            data = {'check': check, 'method': m.name, 'implementation': m.implementation,
                    'expected_sha256': expected_sha, 'expected_bytes': expected_bytes,
                    'passes_requested': passes, 'interrupted': interrupted,
                    'observations': m.observations, 'meaning': meaning,
                    'snapshot_before': before, 'snapshot_after': after}
            data.update(extra or {})
            if m.unsupported:
                rec.record(f'{check}: {m.name}', 'SKIP', m.unsupported, data, category=check)
                continue
            status, summary, stats = evaluate(m.observations, expected_sha, expected_bytes)
            data['stats'] = stats
            if interrupted:
                summary = f'(interrupted after {len(m.observations)} passes) ' + summary
                if status == 'PASS':
                    status = 'WARN'
            rec.record(f'{check}: {m.name}', status, summary, data, category=check, started=t0)


def free_mib(path) -> int:
    return shutil.disk_usage(path).free // hashing.MIB


def writable_dir(path: pathlib.Path) -> bool:
    return path.is_dir() and os.access(path, os.W_OK)
