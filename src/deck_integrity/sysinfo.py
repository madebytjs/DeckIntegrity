"""Hardware/software inventory and passive error counters (no root needed)."""
from __future__ import annotations

import os
import pathlib
import platform
import sys
import time

from deck_integrity import sensors

SYS = pathlib.Path('/sys')
PROC = pathlib.Path('/proc')
CATEGORY = 'System inventory'

# Kernel taint bits worth flagging for hardware/data-integrity work.
TAINT_BITS = {4: 'M (machine check exception)', 5: 'B (bad page referenced)',
              7: 'D (kernel oops/die)', 9: 'W (kernel warning issued)',
              14: 'L (soft lockup)', 0: 'P (proprietary module)', 12: 'O (out-of-tree module)',
              13: 'E (unsigned module)'}
SERIOUS_TAINT = {4, 5, 7, 14}
DMI_FIELDS = ['sys_vendor', 'product_name', 'product_version', 'product_family', 'board_vendor',
              'board_name', 'bios_vendor', 'bios_version', 'bios_date']
LOG_DENIED = ('no journal files', 'insufficient permissions', 'operation not permitted',
              'permission denied', 'read kernel buffer failed')


def _read(p) -> str | None:
    try:
        return pathlib.Path(p).read_text(errors='replace').strip()
    except OSError:
        return None


def os_release() -> dict:
    out = {}
    for line in (_read('/etc/os-release') or '').splitlines():
        k, sep, v = line.partition('=')
        if sep:
            out[k] = v.strip().strip('"')
    return out


def cpu_model() -> str | None:
    for line in (_read(PROC / 'cpuinfo') or '').splitlines():
        if line.lower().startswith('model name'):
            return line.split(':', 1)[1].strip()
    return platform.processor() or None


def dmi() -> dict:
    return {f: _read(SYS / 'class' / 'dmi' / 'id' / f) for f in DMI_FIELDS}


def nvme_devices() -> list:
    out = []
    for d in sorted((SYS / 'class' / 'nvme').glob('nvme*')):
        out.append({'name': d.name, 'model': _read(d / 'model'),
                    'firmware_rev': _read(d / 'firmware_rev')})  # serial deliberately not read
    return out


def mounts() -> list:
    """Mount entries for the filesystems this tool uses."""
    wanted = {'/', '/home', '/dev/shm', '/tmp', '/var'}
    out = []
    for line in (_read(PROC / 'mounts') or '').splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[1] in wanted:
            out.append({'device': parts[0], 'mountpoint': parts[1], 'fstype': parts[2],
                        'options': parts[3]})
    return out


def btrfs_error_stats() -> dict:
    """{'<fsid>/<devid>': {counter: value}} from /sys/fs/btrfs (kernel 5.14+)."""
    out = {}
    for f in sorted((SYS / 'fs' / 'btrfs').glob('*/devinfo/*/error_stats')):
        stats = {}
        for line in (_read(f) or '').splitlines():
            k, _, v = line.partition(' ')
            if v.strip().isdigit():
                stats[k] = int(v)
        out[f'{f.parents[2].name}/{f.parent.name}'] = stats
    return out


def ext4_errors() -> dict:
    out = {}
    for d in sorted((SYS / 'fs' / 'ext4').glob('*')):
        n = _read(d / 'errors_count')
        if n is not None and n.isdigit():
            out[d.name] = {'errors_count': int(n), 'first_error_time': _read(d / 'first_error_time'),
                           'last_error_time': _read(d / 'last_error_time')}
    return out


def taint() -> dict:
    raw = _read(PROC / 'sys' / 'kernel' / 'tainted')
    if raw is None or not raw.isdigit():
        return {'value': None, 'flags': []}
    v = int(raw)
    return {'value': v, 'flags': [TAINT_BITS.get(b, f'bit {b}') for b in range(32) if v >> b & 1],
            'serious': [TAINT_BITS[b] for b in SERIOUS_TAINT if v >> b & 1]}


def collect() -> dict:
    return {
        'collected_at_monotonic': time.monotonic(),
        'os_release': os_release(), 'kernel': platform.release(), 'kernel_version': platform.version(),
        'machine': platform.machine(), 'python': sys.version.split()[0],
        'python_executable': sys.executable, 'cpu_model': cpu_model(), 'cpu_count': os.cpu_count(),
        'meminfo_kib': {k: v for k, v in sensors.meminfo().items()
                        if k in ('MemTotal', 'MemAvailable', 'SwapTotal', 'SwapFree', 'Shmem')},
        'dmi': dmi(), 'nvme': nvme_devices(), 'mounts': mounts(),
        'btrfs_error_stats': btrfs_error_stats(), 'ext4_errors': ext4_errors(), 'taint': taint(),
        'boot_id': _read(PROC / 'sys' / 'kernel' / 'random' / 'boot_id'),
        'snapshot': sensors.snapshot(),
    }


def commands(home: str) -> list:
    return [
        ('Kernel and host', ['uname', '-srvmo']),
        ('Storage layout', ['lsblk', '-o', 'NAME,TYPE,SIZE,FSTYPE,MOUNTPOINTS,MODEL', '-e', '7']),
        ('Home filesystem', ['findmnt', '-T', home]),
        ('Filesystem capacity', ['df', '-hT', home, '/dev/shm', '/tmp']),
        ('CPU information', ['lscpu']),
        ('PCI devices and drivers', ['lspci', '-nnk']),
        ('OpenGL renderer', ['glxinfo', '-B']),
        ('Vulkan summary', ['vulkaninfo', '--summary']),
        ('NVMe device list', ['nvme', 'list']),
        ('SMART device scan', ['smartctl', '--scan']),
        ('Kernel log warnings, current boot', ['journalctl', '-k', '-b', '-p', 'warning',
                                               '--no-pager', '-n', '300']),
        ('Kernel ring buffer errors', ['dmesg', '--level=emerg,alert,crit,err,warn']),
    ]


def run(ctx) -> dict:
    rec = ctx.rec
    rec.section('ENVIRONMENT')
    info = collect()
    rel = info['os_release']
    rec.log(f'OS: {rel.get("PRETTY_NAME", platform.platform())} '
            f'(VERSION_ID={rel.get("VERSION_ID", "?")}, BUILD_ID={rel.get("BUILD_ID", "?")})')
    rec.log(f'Kernel: {info["kernel"]} | Python: {info["python"]} | Machine: {info["machine"]}')
    d = info['dmi']
    rec.log(f'Device: {d.get("sys_vendor")} {d.get("product_name")} {d.get("product_version") or ""}'
            f' | BIOS {d.get("bios_version")} ({d.get("bios_date")})')
    rec.log(f'CPU: {info["cpu_model"]} x{info["cpu_count"]} | '
            f'MemTotal: {info["meminfo_kib"].get("MemTotal", 0) // 1024} MiB')
    for n in info['nvme']:
        rec.log(f'NVMe {n["name"]}: {n["model"]} firmware {n["firmware_rev"]}')
    for m in info['mounts']:
        rec.log(f'Mount {m["mountpoint"]}: {m["device"]} {m["fstype"]} ({m["options"]})')

    for name, argv in commands(str(ctx.home)):
        r = ctx.run_command(argv, timeout=30)
        ctx.rec.command(name, r)
        if r.missing:
            rec.record(name, 'SKIP', argv[0] + ' not installed', category=CATEGORY)
            continue
        body = (r.stdout + ('\n' + r.stderr if r.stderr.strip() else '')).strip()
        rec.log(f'\n--- {name}: {" ".join(argv)} (exit {r.returncode}, {r.elapsed:.1f}s) ---\n{body}')
        denied = any(s in body.lower() for s in LOG_DENIED)
        if r.timed_out:
            rec.record(name, 'WARN', 'timed out', category=CATEGORY)
        elif denied:
            rec.record(name, 'WARN', 'not readable as this user; output may be empty or partial',
                       category=CATEGORY)
        elif r.returncode != 0:
            rec.record(name, 'WARN', f'exit {r.returncode}', category=CATEGORY)
        else:
            rec.record(name, 'INFO', f'collected ({len(r.stdout.splitlines())} lines)', category=CATEGORY)

    _counters(rec, info)
    return info


def _counters(rec, info) -> None:
    cat = 'Filesystem and kernel counters'
    rec.section('FILESYSTEM AND KERNEL ERROR COUNTERS')
    bt = info['btrfs_error_stats']
    if not bt:
        rec.record('Btrfs device error counters', 'SKIP', 'no Btrfs error_stats in /sys', category=cat)
    else:
        nonzero = {dev: {k: v for k, v in s.items() if v} for dev, s in bt.items()}
        nonzero = {k: v for k, v in nonzero.items() if v}
        if nonzero:
            rec.record('Btrfs device error counters', 'WARN',
                       f'non-zero counters {nonzero}. These are cumulative and may predate this session.',
                       {'stats': bt}, category=cat)
        else:
            rec.record('Btrfs device error counters', 'PASS', f'all zero across {len(bt)} device(s)',
                       {'stats': bt}, category=cat)
    ex = info['ext4_errors']
    if not ex:
        rec.record('ext4 error counters', 'SKIP', 'no ext4 filesystems found in /sys', category=cat)
    else:
        bad = {k: v for k, v in ex.items() if v['errors_count']}
        rec.record('ext4 error counters', 'WARN' if bad else 'PASS',
                   (f'errors recorded: {bad}. Cumulative; may predate this session.' if bad
                    else f'zero errors on {", ".join(ex)}'), {'ext4': ex}, category=cat)
    t = info['taint']
    if t['value'] is None:
        rec.record('Kernel taint', 'SKIP', 'not readable', category=cat)
    else:
        rec.record('Kernel taint', 'WARN' if t['serious'] else 'INFO',
                   f'value {t["value"]}: {", ".join(t["flags"]) or "not tainted"}', {'taint': t},
                   category=cat)
