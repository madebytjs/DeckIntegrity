"""Health overview: one card per area, each backed by a real measurement.

Cards never invent a score. The data checks say whether problems were seen;
battery and SSD cards show the numbers the hardware reports; anything that
was not measured says so.
"""
from __future__ import annotations

from deck_integrity import health

TEMP_WARN_C = 90
DATA_CARDS = [('cpu_mem', 'Processor and memory', '2 GiB /dev/zero stream'),
              ('memory', 'Memory (RAM)', 'RAM-backed file'),
              ('storage', 'Storage (SSD) data', 'SSD test file')]
FILESYSTEM_TESTS = ('Btrfs device error counters', 'ext4 error counters')
ORDER = {'bad': 0, 'warn': 1, 'good': 2, 'pending': 3, 'unknown': 4}


def card(key, title, status, value, detail) -> dict:
    return {'key': key, 'title': title, 'status': status, 'value': value, 'detail': detail}


def collect_hardware(ctx) -> None:
    """Battery and SSD health readings, taken once at the start of a run."""
    rec = ctx.rec
    rec.section('HARDWARE HEALTH')
    bat = health.battery_health()
    b_status, b_value, b_detail = health.battery_verdict(bat)
    rec.record('Battery health', 'INFO' if bat else 'SKIP', f'{b_value}. {b_detail}',
               {'battery': bat}, category='Hardware health')
    ssd = health.read_ssd_health(ctx, elevated=bool(getattr(ctx.args, 'ssd_health', False)))
    if not ssd:
        rec.record('SSD health log', 'SKIP', 'no NVMe drive found', category='Hardware health')
    for entry in ssd:
        status, value, detail = health.ssd_verdict(entry['smart'], entry['reason'])
        level = {'good': 'PASS', 'warn': 'WARN', 'bad': 'WARN'}.get(status, 'SKIP')
        rec.record(f'SSD health log ({entry["device"]})', level, f'{value}. {detail}',
                   {'ssd': entry}, category='Hardware health')
    rec.run_info['hardware'] = {'battery': bat, 'ssd': ssd}


def _data_card(rec, key, title, category, final) -> dict:
    rows = [r for r in rec.results if r['category'] == category and r['status'] != 'INFO']
    statuses = {r['status'] for r in rows}
    passes = sum(len((r.get('data') or {}).get('observations') or []) for r in rows)
    if 'FAIL' in statuses:
        bad = [r for r in rows if r['status'] == 'FAIL']
        return card(key, title, 'bad', 'Problems found',
                    f'{len(bad)} of {len(rows)} methods saw data that did not match: '
                    + '; '.join(r['test'].split(': ', 1)[-1] for r in bad) + '.')
    if not rows or statuses <= {'SKIP'}:
        if not final:
            return card(key, title, 'pending', 'Testing...', 'This check has not finished yet.')
        reason = rows[0]['summary'] if rows else 'This check was not run.'
        return card(key, title, 'unknown', 'Not tested', reason)
    if 'ERROR' in statuses:
        return card(key, title, 'warn', 'Incomplete', 'Some passes could not complete; see the report.')
    if any((r.get('data') or {}).get('interrupted') for r in rows):
        return card(key, title, 'warn', 'Stopped early',
                    f'No problems in the {passes} passes that ran before the stop.')
    return card(key, title, 'good', 'No problems found',
                f'All {passes} passes matched exactly. This lowers the odds of a fault here but '
                'cannot rule out one that comes and goes.')


def _filesystem_card(rec) -> dict:
    rows = [r for r in rec.results if r['test'] in FILESYSTEM_TESTS]
    title = 'Filesystem errors'
    if not rows or all(r['status'] == 'SKIP' for r in rows):
        return card('filesystem', title, 'unknown', 'Not measured', 'No error counters were readable.')
    warn = [r for r in rows if r['status'] == 'WARN']
    if warn:
        return card('filesystem', title, 'warn', 'Errors recorded',
                    ' '.join(r['summary'] for r in warn))
    return card('filesystem', title, 'good', 'None recorded',
                'The filesystems have not recorded any errors.')


def _peak_temp(rec) -> float | None:
    temps = []
    for r in rec.results:
        data = r.get('data') or {}
        temps += [o.get('max_temp_c') for o in data.get('observations') or []]
        for snap in (data.get('snapshot_before'), data.get('snapshot_after')):
            temps += list(((snap or {}).get('temps_c') or {}).values())
    temps = [t for t in temps if isinstance(t, (int, float))]
    return max(temps) if temps else None


def _temp_card(rec) -> dict:
    peak = _peak_temp(rec)
    if peak is None:
        return card('temps', 'Temperatures', 'unknown', 'Not measured', 'No temperature sensors were readable.')
    status = 'warn' if peak >= TEMP_WARN_C else 'good'
    note = (' Running hot under load; check vents and fan.' if status == 'warn'
            else ' Within normal range under load.')
    return card('temps', 'Temperatures', status, f'Peak {peak:.0f} °C',
                f'Highest sensor reading during the run was {peak:.0f} °C.' + note)


def cards(rec, final: bool = True) -> list:
    hw = rec.run_info.get('hardware') or {}
    out = [_data_card(rec, k, t, c, final) for k, t, c in DATA_CARDS]
    ssd = hw.get('ssd') or []
    if ssd:
        # Show the worst drive if there are several.
        verdicts = [(health.ssd_verdict(e['smart'], e['reason']), e['device']) for e in ssd]
        (status, value, detail), dev = min(verdicts, key=lambda v: ORDER[v[0][0]])
        out.append(card('ssd_health', 'SSD health', status, value,
                        detail + (f' ({dev})' if len(ssd) > 1 else '')))
    else:
        out.append(card('ssd_health', 'SSD health', 'unknown', 'Not measured',
                        'The SSD health log was not read.'))
    b_status, b_value, b_detail = health.battery_verdict(hw.get('battery')) if 'battery' in hw \
        else ('unknown', 'Not measured', 'Battery health was not read.')
    out.append(card('battery', 'Battery', b_status, b_value, b_detail))
    out.append(_filesystem_card(rec))
    out.append(_temp_card(rec))
    return out
