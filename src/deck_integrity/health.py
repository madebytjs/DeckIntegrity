"""Hardware health readings: battery wear and the SSD's own health log.

Read-only. Battery data comes from sysfs and needs no privileges. The SSD
health log (NVMe SMART) is normally readable only by root, so it is tried
unprivileged first and only read with admin rights when the user explicitly
opts in (--ssd-health); the system's own password prompt (pkexec) asks.
"""
from __future__ import annotations

import json
import pathlib
import re
import shutil

SYS = pathlib.Path('/sys')
NVME_NAME = re.compile(r'^nvme\d+$')   # controller names only; nothing else reaches a command line
SMART_TIMEOUT_S = 180                  # includes time for the user to type a password
PKEXEC_DISMISSED = {126, 127}          # pkexec: dialog dismissed / not authorised

BATTERY_GOOD_PCT = 80                  # common "worn battery" threshold
BATTERY_POOR_PCT = 60
SSD_WEAR_WARN_PCT = 80                 # NVMe "percentage used" of rated endurance
SSD_WEAR_BAD_PCT = 100


def _read(p) -> str | None:
    try:
        return pathlib.Path(p).read_text(errors='replace').strip()
    except OSError:
        return None


def _int(v) -> int | None:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


# -- battery ----------------------------------------------------------------

def battery_health(root: pathlib.Path = SYS) -> dict | None:
    """Capacity now vs when new, from sysfs (energy_* in uWh or charge_* in uAh)."""
    for ps in sorted((root / 'class' / 'power_supply').glob('*')):
        if (_read(ps / 'type') or '').lower() != 'battery':
            continue
        full = design = None
        unit = None
        for prefix, u in (('energy', 'Wh'), ('charge', 'Ah')):
            f, d = _int(_read(ps / f'{prefix}_full')), _int(_read(ps / f'{prefix}_full_design'))
            if f and d:
                full, design, unit = f, d, u
                break
        out = {'name': ps.name, 'model': _read(ps / 'model_name'),
               'manufacturer': _read(ps / 'manufacturer'), 'technology': _read(ps / 'technology'),
               'cycle_count': _int(_read(ps / 'cycle_count')),
               'charge_pct': _int(_read(ps / 'capacity')), 'status': _read(ps / 'status'),
               'full': full, 'design': design, 'unit': unit, 'health_pct': None}
        if full and design:
            # Some packs report slightly above design when new; cap the display at 100.
            out['health_pct'] = round(min(100.0, 100.0 * full / design), 1)
            out['full_display'] = f'{full / 1e6:.1f} {unit}'
            out['design_display'] = f'{design / 1e6:.1f} {unit}'
        if out['cycle_count'] is not None and out['cycle_count'] <= 0:
            out['cycle_count'] = None   # many firmwares report 0 when they do not track it
        return out
    return None


def battery_verdict(b: dict | None) -> tuple:
    """(status, value, detail) with status good|warn|bad|unknown."""
    if not b:
        return 'unknown', 'No battery', 'No battery was found.'
    pct = b.get('health_pct')
    cycles = f', {b["cycle_count"]} charge cycles' if b.get('cycle_count') else ''
    if pct is None:
        return 'unknown', 'Not reported', 'The battery does not report its design capacity.'
    detail = (f'Holds {b["full_display"]} when full, against {b["design_display"]} when new '
              f'({pct:g}%){cycles}.')
    if pct >= BATTERY_GOOD_PCT:
        return 'good', f'{pct:.0f}% of original', detail
    if pct >= BATTERY_POOR_PCT:
        return 'warn', f'{pct:.0f}% of original', detail + ' Noticeably worn.'
    return 'bad', f'{pct:.0f}% of original', detail + ' Heavily worn.'


# -- SSD health log -----------------------------------------------------------

def nvme_devices(root: pathlib.Path = SYS) -> list:
    return [d.name for d in sorted((root / 'class' / 'nvme').glob('nvme*')) if NVME_NAME.match(d.name)]


def smart_command(device: str, elevated: bool) -> list | None:
    """smartctl JSON read of one NVMe controller, optionally through pkexec."""
    if not NVME_NAME.match(device):
        raise ValueError('refusing unexpected device name: ' + repr(device))
    smartctl = shutil.which('smartctl')
    if not smartctl:
        return None
    cmd = [smartctl, '-j', '-a', '/dev/' + device]
    if elevated:
        pkexec = shutil.which('pkexec')
        if not pkexec:
            return None
        cmd = [pkexec] + cmd   # pkexec wants the absolute program path, which which() gives
    return cmd


def parse_smartctl(text: str) -> dict:
    """Pick the health fields from smartctl -j output. Never keeps the serial number."""
    data = json.loads(text)
    log = data.get('nvme_smart_health_information_log')
    if not isinstance(log, dict):
        msgs = [m.get('string', '') for m in (data.get('smartctl') or {}).get('messages', [])]
        raise ValueError('no NVMe health log in smartctl output: ' + '; '.join(msgs)[:300])
    pick = ('critical_warning', 'temperature', 'available_spare', 'available_spare_threshold',
            'percentage_used', 'data_units_read', 'data_units_written', 'power_on_hours',
            'power_cycles', 'unsafe_shutdowns', 'media_errors', 'num_err_log_entries')
    out = {k: log.get(k) for k in pick}
    out['model'] = data.get('model_name')
    out['firmware'] = data.get('firmware_version')
    out['smart_passed'] = (data.get('smart_status') or {}).get('passed')
    if isinstance(out['data_units_written'], int):
        out['tb_written'] = round(out['data_units_written'] * 512000 / 1e12, 2)   # 1 unit = 512,000 bytes
    return out


def ssd_verdict(s: dict | None, reason: str = '') -> tuple:
    if not s:
        return 'unknown', 'Not measured', reason or 'The SSD health log was not read.'
    used = s.get('percentage_used')
    spare, thresh = s.get('available_spare'), s.get('available_spare_threshold')
    media, crit = s.get('media_errors') or 0, s.get('critical_warning') or 0
    parts = []
    if used is not None:
        parts.append(f'{used}% of rated endurance used')
    if spare is not None:
        parts.append(f'spare {spare}% (minimum {thresh}%)')
    parts.append(f'{media} media errors')
    if s.get('tb_written') is not None:
        parts.append(f'{s["tb_written"]:g} TB written')
    if s.get('power_on_hours') is not None:
        parts.append(f'{s["power_on_hours"]} power-on hours')
    if s.get('unsafe_shutdowns') is not None:
        parts.append(f'{s["unsafe_shutdowns"]} unsafe shutdowns')
    detail = 'The SSD reports: ' + ', '.join(parts) + '.'
    value = f'{used}% worn' if used is not None else 'Read'
    if crit or media or s.get('smart_passed') is False or \
            (spare is not None and thresh is not None and spare < thresh):
        why = []
        if crit:
            why.append(f'critical warning flags {crit}')
        if media:
            why.append('media errors recorded')
        if spare is not None and thresh is not None and spare < thresh:
            why.append('spare capacity below its threshold')
        if s.get('smart_passed') is False:
            why.append('the drive reports its own health check failed')
        return 'bad', value, detail + ' Problem: ' + ', '.join(why) + '.'
    if used is not None and used >= SSD_WEAR_BAD_PCT:
        return 'bad', value, detail + ' Past its rated endurance.'
    if used is not None and used >= SSD_WEAR_WARN_PCT:
        return 'warn', value, detail + ' Approaching its rated endurance.'
    return 'good', value, detail


def read_ssd_health(ctx, elevated: bool) -> list:
    """One {device, smart or None, reason, elevated} entry per NVMe controller."""
    results = []
    for dev in nvme_devices():
        entry = {'device': dev, 'smart': None, 'reason': '', 'elevated': False}
        cmd = smart_command(dev, elevated=False)
        if not cmd:
            entry['reason'] = 'smartctl is not installed.'
            results.append(entry)
            continue
        r = ctx.run_command(cmd, timeout=30)
        ctx.rec.command(f'SSD health log {dev}', r)
        try:
            entry['smart'] = parse_smartctl(r.stdout)
        except ValueError:
            if not elevated:
                entry['reason'] = ('Reading the SSD health log needs admin rights. Tick "SSD health '
                                   'report" (or use --ssd-health) to allow it.')
            else:
                ecmd = smart_command(dev, elevated=True)
                if not ecmd:
                    entry['reason'] = 'pkexec is not available to ask for admin rights.'
                else:
                    ctx.rec.log(f'  Asking for admin rights to read the {dev} health log...')
                    er = ctx.run_command(ecmd, timeout=SMART_TIMEOUT_S)
                    ctx.rec.command(f'SSD health log {dev} (admin)', er)
                    entry['elevated'] = True
                    try:
                        entry['smart'] = parse_smartctl(er.stdout)
                    except ValueError as e:
                        if er.returncode in PKEXEC_DISMISSED:
                            entry['reason'] = ('Admin rights were not granted. On a Steam Deck, set '
                                               'a password first with "passwd" in Konsole.')
                        else:
                            entry['reason'] = f'Could not read the health log: {e}'
        results.append(entry)
    return results
