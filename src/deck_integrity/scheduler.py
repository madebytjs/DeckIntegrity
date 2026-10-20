"""Runs prepared checks round by round, for a pass count or a duration.

A round is one pass of every method of every check, interleaved, so a fault
that comes and goes over time shows up across checks and methods instead of
landing on whichever ran last. Endurance mode just keeps doing rounds until
the time is up; the test data is created once and only read afterwards.
"""
from __future__ import annotations

import threading
import time
import traceback
from typing import Callable

from deck_integrity.checks.common import CheckPlan, Method, evaluate

LIVE_SAVE_EVERY_S = 10


class Stopped(KeyboardInterrupt):
    """Stop requested by the controller (GUI); handled exactly like Ctrl+C."""


class Control:
    """Pause/stop requests from another thread. Checked between operations only,
    so a pause never distorts the timing of a running hash."""

    def __init__(self):
        self._run = threading.Event()
        self._run.set()
        self.stop_requested = False
        self.paused_s = 0.0

    def pause(self):
        self._run.clear()

    def resume(self):
        self._run.set()

    def stop(self):
        self.stop_requested = True
        self._run.set()

    @property
    def paused(self) -> bool:
        return not self._run.is_set()

    def checkpoint(self, on_pause: Callable | None = None) -> None:
        if self.paused:
            t0 = time.monotonic()
            if on_pause:
                on_pause(True)
            self._run.wait()
            self.paused_s += time.monotonic() - t0
            if on_pause:
                on_pause(False)
        if self.stop_requested:
            raise Stopped('stop requested')


def _active(plans) -> list:
    return [(p, m) for p in plans for m in p.methods if not m.unsupported]


def run_checks(ctx, preparers: list, passes: int | None = None, duration_s: float | None = None) -> None:
    """Prepare every check, run them, record results, always clean up."""
    rec = ctx.rec
    plans = []
    try:
        for prep in preparers:
            name, prep = prep if isinstance(prep, tuple) else (prep.__module__.split('.')[-1], prep)
            try:
                plan = prep(ctx)
            except Exception as e:
                rec.record(f'{name} check', 'ERROR', 'setup failed: ' + repr(e),
                           {'traceback': traceback.format_exc()}, category='setup')
                continue
            if plan:
                plans.append(plan)
        if plans:
            execute(ctx, plans, passes=passes, duration_s=duration_s)
    finally:
        for p in plans:
            if p.cleanup:
                try:
                    p.cleanup()
                except Exception as e:
                    rec.log(f'  cleanup of {p.name} failed: {e!r}')


def execute(ctx, plans: list, passes: int | None = None, duration_s: float | None = None) -> None:
    """Round-robin all methods of all plans; record one result per method at the end."""
    if (passes is None) == (duration_s is None):
        raise ValueError('give exactly one of passes or duration_s')
    rec = ctx.rec
    control = getattr(ctx, 'control', None) or Control()
    before = ctx.snapshot()
    t0 = time.monotonic()
    paused_at_start = control.paused_s
    state = {'first_failure': None, 'failures': [], 'rounds': 0, 'interrupted': False,
             'saved_at': 0.0}

    def active_elapsed() -> float:
        return time.monotonic() - t0 - (control.paused_s - paused_at_start)

    def done() -> bool:
        if passes is not None:
            return state['rounds'] >= passes
        return active_elapsed() >= duration_s

    def progress() -> None:
        el = active_elapsed()
        if passes is not None:
            n_active = max(1, len(_active(plans)))
            completed = sum(len(m.observations) for p in plans for m in p.methods)
            frac = min(1.0, completed / (passes * n_active))
        else:
            frac = min(1.0, el / duration_s)
        eta = (el / frac - el) if frac > 0 else None
        rec.emit('progress', fraction=round(frac, 4), round=state['rounds'], elapsed_s=round(el, 1),
                 eta_s=round(eta, 1) if eta is not None else None)

    def on_pause(paused):
        rec.emit('paused' if paused else 'resumed')
        rec.log('  (paused)' if paused else '  (resumed)')

    label = f'{passes} passes' if passes is not None else f'endurance, {duration_s / 60:g} minutes'
    rec.log(f'\nRunning {len(plans)} check(s), {len(_active(plans))} methods, {label}.')
    try:
        while not done():
            r = state['rounds'] + 1
            for plan in plans:
                for m in plan.methods:
                    if m.unsupported:
                        continue
                    control.checkpoint(on_pause)
                    if passes is None and done():
                        break
                    _one(ctx, rec, plan, m, r, passes, active_elapsed, state)
            state['rounds'] = r
            rec.live = {p.name: {m.name: m.observations for m in p.methods} for p in plans}
            if r == 1 or time.monotonic() - state['saved_at'] >= LIVE_SAVE_EVERY_S:
                rec.save()   # crash-safe evidence mid-run, without rewriting every round
                state['saved_at'] = time.monotonic()
            progress()
            if not _active(plans):
                break
    except KeyboardInterrupt:
        state['interrupted'] = True
        raise
    finally:
        rec.live = None
        rec.run_info.update({'scheduler': {
            'mode': 'passes' if passes is not None else 'endurance', 'passes': passes,
            'duration_s': duration_s, 'active_s': round(active_elapsed(), 1),
            'paused_s': round(control.paused_s - paused_at_start, 1), 'rounds': state['rounds'],
            'interrupted': state['interrupted'], 'first_failure': state['first_failure'],
            'failure_times_s': state['failures']}})
        _record_all(ctx, plans, passes, duration_s, before, t0, state)


def _one(ctx, rec, plan: CheckPlan, m: Method, r: int, passes, active_elapsed, state) -> None:
    obs = m.fn(r)
    obs['pass'] = r
    snap = ctx.snapshot() or {}
    temps = snap.get('temps_c') or {}
    obs['uptime_s'] = snap.get('uptime_s')
    obs['max_temp_c'] = max(temps.values()) if temps else None
    obs['at_s'] = round(active_elapsed(), 1)
    if obs.get('unsupported'):
        m.unsupported = obs['unsupported']
        rec.log(f'  {m.name}: not available ({m.unsupported})')
        rec.emit('unsupported', check=plan.name, method=m.name, reason=m.unsupported)
        return
    m.observations.append(obs)
    exp, nb = plan.expected_sha, plan.expected_bytes
    mismatch = not obs.get('error') and (
        (exp and obs['sha256'] != exp) or (nb and obs.get('bytes') is not None and obs['bytes'] != nb))
    status = 'error' if obs.get('error') else ('mismatch' if mismatch else 'ok')
    of = f'/{passes}' if passes is not None else ''
    nbytes = f'{obs["bytes"]} bytes, ' if obs.get('bytes') is not None else ''
    flag = {'ok': '', 'error': '  <-- ERROR', 'mismatch': '  <-- MISMATCH'}[status]
    rec.log(f'  [{plan.name}] {m.name} pass {r}{of}: {obs["sha256"] or "-"} '
            f'({nbytes}{obs["elapsed_s"]}s){flag}' + (f' {obs["error"]}' if obs.get('error') else ''))
    if obs.get('chunk_mismatches'):
        offs = ', '.join(str(c['offset_mib']) for c in obs['chunk_mismatches'][:8])
        rec.log(f'    differing 32 MiB regions at MiB offsets: {offs}'
                f'{" ..." if obs["chunk_mismatch_count"] > 8 else ""}')
    if mismatch:
        state['failures'].append(obs['at_s'])
        if not state['first_failure']:
            state['first_failure'] = {'check': plan.name, 'method': m.name, 'pass': r,
                                      'at_s': obs['at_s'], 'uptime_s': obs['uptime_s'],
                                      'max_temp_c': obs['max_temp_c']}
            rec.log(f'  FIRST FAILURE: {plan.name} / {m.name}, pass {r}, {obs["at_s"]}s into the run')
    rec.emit('pass', check=plan.name, method=m.name, round=r, passes=passes, status=status,
             sha256=obs['sha256'], expected=exp, error=obs.get('error') or '',
             elapsed_s=obs['elapsed_s'], max_temp_c=obs['max_temp_c'], at_s=obs['at_s'],
             chunk_mismatch_count=obs.get('chunk_mismatch_count', 0))


def _record_all(ctx, plans, passes, duration_s, before, t0, state) -> None:
    rec = ctx.rec
    after = ctx.snapshot()
    for plan in plans:
        for m in plan.methods:
            data = {'check': plan.name, 'method': m.name, 'implementation': m.implementation,
                    'expected_sha256': plan.expected_sha, 'expected_bytes': plan.expected_bytes,
                    'passes_requested': passes, 'duration_s': duration_s,
                    'interrupted': state['interrupted'], 'observations': m.observations,
                    'meaning': plan.meaning, 'snapshot_before': before, 'snapshot_after': after}
            data.update(plan.extra)
            name = f'{plan.name}: {m.name}'
            if m.unsupported:
                rec.record(name, 'SKIP', m.unsupported, data, category=plan.name)
                continue
            status, summary, stats = evaluate(m.observations, plan.expected_sha, plan.expected_bytes)
            data['stats'] = stats
            if state['interrupted']:
                summary = f'(interrupted after {len(m.observations)} passes) ' + summary
                if status == 'PASS':
                    status = 'WARN'
            rec.record(name, status, summary, data, category=plan.name, started=t0)
