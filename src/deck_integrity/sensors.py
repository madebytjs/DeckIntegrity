"""Unprivileged sysfs snapshots: temperatures, battery, uptime, memory."""
from __future__ import annotations

import pathlib

SYS = pathlib.Path('/sys')
PROC = pathlib.Path('/proc')


def _read(p: pathlib.Path) -> str | None:
    try:
        return p.read_text(errors='replace').strip()
    except OSError:
        return None


def temperatures(root: pathlib.Path = SYS) -> dict:
    """{'<chip>/<label>': degrees C} from /sys/class/hwmon."""
    out = {}
    for hw in sorted((root / 'class' / 'hwmon').glob('hwmon*')):
        chip = _read(hw / 'name') or hw.name
        for t in sorted(hw.glob('temp*_input')):
            raw = _read(t)
            if raw is None or not raw.lstrip('-').isdigit():
                continue
            label = _read(t.with_name(t.name.replace('_input', '_label'))) or t.name.replace('_input', '')
            out[f'{chip}/{label}'] = int(raw) / 1000
    return out


def battery(root: pathlib.Path = SYS) -> dict | None:
    for ps in sorted((root / 'class' / 'power_supply').glob('*')):
        if (_read(ps / 'type') or '').lower() != 'battery':
            continue
        cap = _read(ps / 'capacity')
        return {'name': ps.name, 'capacity_pct': int(cap) if cap and cap.isdigit() else None,
                'status': _read(ps / 'status')}
    return None


def meminfo(root: pathlib.Path = PROC) -> dict:
    """MemInfo values in KiB."""
    out = {}
    for line in (_read(root / 'meminfo') or '').splitlines():
        k, _, v = line.partition(':')
        parts = v.split()
        if parts and parts[0].isdigit():
            out[k.strip()] = int(parts[0])
    return out


def uptime_seconds(root: pathlib.Path = PROC) -> float | None:
    raw = _read(root / 'uptime')
    try:
        return float(raw.split()[0]) if raw else None
    except ValueError:
        return None


def snapshot() -> dict:
    """Cheap state capture recorded before and after each check."""
    mem = meminfo()
    return {'uptime_s': uptime_seconds(), 'temps_c': temperatures(), 'battery': battery(),
            'mem_available_mib': mem['MemAvailable'] // 1024 if 'MemAvailable' in mem else None}
