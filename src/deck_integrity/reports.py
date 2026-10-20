"""Final report files: system-info.json, failures.txt, support-summary.txt.

Wording rule: report what was observed. Never name a defective component
from a hash result alone.
"""
from __future__ import annotations

import pathlib

from deck_integrity import VERSION
from deck_integrity.recorder import Recorder, write_json

INVENTORY = {'System inventory'}
LIMITATIONS = [
    'A PASS means no fault was observed during this run. Intermittent faults can be missed; '
    'a passing session does not certify the device as healthy.',
    'A failed hash is evidence of a data-integrity fault somewhere in the tested path. It does '
    'not by itself identify the faulty component.',
    'A clean SMART report or zero filesystem error counters do not prove the storage is fault-free.',
    'This version inventories the GPU and CPU but does not stress them, and the RAM check is a spot '
    'check rather than an exhaustive memory test.',
    'Redaction of personal identifiers is best effort. Review the files before posting them publicly.',
]


def _device_line(info: dict) -> str:
    d = info.get('dmi') or {}
    rel = info.get('os_release') or {}
    storage = '; '.join(f'{n["name"]} {n["model"]} fw {n["firmware_rev"]}'
                        for n in info.get('nvme') or []) or '?'
    return (f'Device: {d.get("sys_vendor") or "?"} {d.get("product_name") or "?"} '
            f'(board {d.get("board_name") or "?"}, BIOS {d.get("bios_version") or "?"})\n'
            f'OS: {rel.get("PRETTY_NAME", "?")} VERSION_ID={rel.get("VERSION_ID", "?")} '
            f'BUILD_ID={rel.get("BUILD_ID", "?")}\n'
            f'Kernel: {info.get("kernel", "?")}\n'
            f'CPU: {info.get("cpu_model", "?")}\n'
            f'Storage: {storage}')


def _observed(r: dict) -> list:
    data = r.get('data') or {}
    exp = data.get('expected_sha256')
    lines = []
    for o in data.get('observations') or []:
        if o.get('error'):
            lines.append(f'    pass {o["pass"]}: ERROR {o["error"]}')
        elif exp and o.get('sha256') != exp:
            extra = f', {o["chunk_mismatch_count"]} differing 32 MiB regions' \
                if o.get('chunk_mismatch_count') else ''
            temp = f', max temp {o["max_temp_c"]} C' if o.get('max_temp_c') is not None else ''
            lines.append(f'    pass {o["pass"]}: {o["sha256"]} ({o.get("bytes")} bytes{extra}{temp})')
        elif data.get('expected_bytes') and o.get('bytes') not in (None, data['expected_bytes']):
            lines.append(f'    pass {o["pass"]}: wrong byte count {o["bytes"]}')
    return lines


def _mmss(seconds) -> str:
    seconds = int(seconds or 0)
    return f'{seconds // 3600}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}'


def _schedule_lines(s: dict) -> list:
    """Run shape, and for endurance runs how faults were spread over time."""
    if not s:
        return []
    out = []
    if s.get('mode') == 'endurance':
        planned = (s.get('duration_s') or 0) / 60
        out.append(f'ENDURANCE RUN: planned {planned:g} min, ran {_mmss(s.get("active_s"))}'
                   f' (h:mm:ss) over {s.get("rounds")} rounds'
                   + (f', paused {_mmss(s.get("paused_s"))}' if s.get('paused_s') else '')
                   + (' - stopped early' if s.get('interrupted') else ''))
    times = s.get('failure_times_s') or []
    ff = s.get('first_failure')
    if ff:
        temp = f', max temp {ff["max_temp_c"]} C' if ff.get('max_temp_c') is not None else ''
        out.append(f'First failure: {ff["check"]} / {ff["method"]}, pass {ff["pass"]}, '
                   f'{_mmss(ff["at_s"])} into the run{temp}')
        out.append(f'Failed passes: {len(times)}, at ' +
                   ', '.join(_mmss(t) for t in times[:20]) + (' ...' if len(times) > 20 else ''))
    return out + ([''] if out else [])


def failures_text(rec: Recorder) -> str:
    bad = [r for r in rec.results if r['status'] in ('FAIL', 'ERROR')]
    if not bad:
        return 'No failures or errors were observed in this run.\n'
    out = []
    for r in bad:
        out.append(f'[{r["status"]}] {r["test"]}\n  {r["summary"]}')
        exp = (r.get('data') or {}).get('expected_sha256')
        if exp:
            out.append(f'  expected: {exp}')
        out.extend(_observed(r))
        out.append('')
    return '\n'.join(out) + '\n'


def support_summary(rec: Recorder, info: dict) -> str:
    c = rec.counts()
    tests = [r for r in rec.results if r['category'] not in INVENTORY]
    bad = [r for r in tests if r['status'] in ('FAIL', 'ERROR')]
    warn = [r for r in rec.results if r['status'] == 'WARN']
    lines = [f'Deck Integrity Suite {VERSION} - support summary', '',
             f'Run: {rec.started} to {rec.finished or "?"} (UTC), state: {rec.state}',
             _device_line(info), '',
             'Result counts: ' + ', '.join(f'{k} {v}' for k, v in c.items() if v), '']
    lines += _schedule_lines(rec.run_info.get('scheduler') or {})
    if bad:
        lines.append('FAULTS OBSERVED')
        meanings = {}
        for r in bad:
            data = r.get('data') or {}
            lines.append(f'- {r["test"]}: {r["summary"]}')
            if data.get('expected_sha256'):
                lines.append(f'    expected: {data["expected_sha256"]}')
            lines.extend(_observed(r)[:12])
            if data.get('meaning'):
                meanings[data.get('check')] = data['meaning']
        lines += ['', 'WHAT THESE CHECKS MEAN']
        lines += [f'- {k}: {v}' for k, v in meanings.items()]
    else:
        lines.append('No integrity faults were observed in this run.')
    passed = [r for r in tests if r['status'] == 'PASS']
    if passed:
        lines += ['', 'PASSED'] + [f'- {r["test"]}: {r["summary"]}' for r in passed]
    if warn:
        lines += ['', 'WARNINGS (incomplete data or counters worth noting)']
        lines += [f'- {r["test"]}: {r["summary"]}' for r in warn]
    lines += ['', 'LIMITATIONS'] + [f'- {x}' for x in LIMITATIONS]
    lines += ['', 'Full detail: report.txt and results.json in the same folder.']
    return '\n'.join(lines) + '\n'


def write_all(rec: Recorder, info: dict) -> None:
    out: pathlib.Path = rec.out
    rec.save()
    write_json(out / 'system-info.json', rec.redact_obj(info))
    (out / 'failures.txt').write_text(rec.redact(failures_text(rec)), encoding='utf-8')
    (out / 'support-summary.txt').write_text(rec.redact(support_summary(rec, info)), encoding='utf-8')
