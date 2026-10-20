"""Command-line entry point."""
from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import re
import signal
import sys
import threading
import time
import traceback

from deck_integrity import DISCLAIMER, VERSION, reports, runner, sensors, sysinfo
from deck_integrity.checks import ram, ssd, zero
from deck_integrity.checks.common import Context
from deck_integrity.recorder import Recorder, utc_now
from deck_integrity.scheduler import Control, run_checks
from deck_integrity.redact import Redactor

CHECKS = {'zero': zero.prepare, 'ram': ram.prepare, 'ssd': ssd.prepare}
EXIT_OK, EXIT_FAIL, EXIT_ERROR, EXIT_NOT_CONFIRMED, EXIT_INTERRUPTED = 0, 1, 2, 3, 130
LOW_BATTERY_PCT = 20
DEFAULTS = {'passes': 5, 'file_mib': 2048, 'ram_mib': 256}
QUICK = {'passes': 3, 'file_mib': 512, 'ram_mib': 128}
# DMI product names Valve uses for the Steam Deck boards.
VALVE_MODELS = {'jupiter': 'Steam-Deck-LCD', 'galileo': 'Steam-Deck-OLED'}
PLACEHOLDER_DMI = {'', 'default string', 'system product name', 'to be filled by o.e.m.', 'none'}
NOT_CONFIRMED_MSG = ('DeckIntegrity did not run: it was started without a terminal '
                     '(for example by double-clicking it). It writes a large test file, so it only '
                     'runs from Konsole, or with --yes.')


class Terminated(KeyboardInterrupt):
    """SIGTERM/SIGHUP (window closed, Steam stopped the app) treated like Ctrl+C."""


def _on_signal(signum, _frame):
    raise Terminated(signal.Signals(signum).name)


def install_signal_handlers() -> None:
    for name in ('SIGTERM', 'SIGHUP'):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), _on_signal)


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog='deck-integrity',
        description='Non-destructive SteamOS/Linux data-integrity diagnostics with logged results.',
        epilog='Use at your own risk. ' + DISCLAIMER)
    p.add_argument('--version', action='version', version=f'%(prog)s {VERSION}')
    p.add_argument('--quick', action='store_true',
                   help='short run: 3 passes, 512 MiB SSD file, 128 MiB RAM file')
    p.add_argument('--passes', type=int, help='repetitions per method (default 5)')
    p.add_argument('--file-mib', type=int, help='SSD test file size in MiB (default 2048)')
    p.add_argument('--ram-mib', type=int, help='RAM-backed test file size in MiB (default 256)')
    p.add_argument('--skip', action='append', default=[], choices=sorted(CHECKS) + ['sysinfo'],
                   help='skip a check (repeatable)')
    p.add_argument('--output', help='report directory (default ~/deck-integrity-reports/<timestamp>)')
    p.add_argument('--keep-file', action='store_true', help='keep the SSD test file afterwards')
    p.add_argument('--endurance', type=int, metavar='MINUTES',
                   help='repeat the checks for this many minutes to catch intermittent faults '
                        '(the SSD file is written once, then only read)')
    p.add_argument('--yes', action='store_true',
                   help='run even without a terminal (scripts, launchers); never needed in Konsole')
    p.add_argument('--events', action='store_true',
                   help='machine interface for the GUI: JSON lines on stdout; reads '
                        'pause/resume/stop commands from stdin')
    a = p.parse_args(argv)
    preset = QUICK if a.quick else DEFAULTS
    for k, v in preset.items():
        if getattr(a, k) is None:
            setattr(a, k, v)
    if not 1 <= a.passes <= 100:
        p.error('--passes must be 1-100')
    if not 32 <= a.file_mib <= 16384:
        p.error('--file-mib must be 32-16384')
    if not 32 <= a.ram_mib <= 4096:
        p.error('--ram-mib must be 32-4096')
    if a.endurance is not None and not 1 <= a.endurance <= 1440:
        p.error('--endurance must be 1-1440 minutes')
    return a


def _slug(s: str) -> str:
    return re.sub(r'[^A-Za-z0-9.]+', '-', s).strip('-.')


def device_label(dmi: dict) -> str:
    """Human name for the hardware, e.g. Steam-Deck-OLED or Legion-Go-S-8ARP1."""
    vendor = (dmi.get('sys_vendor') or '').strip()
    product = (dmi.get('product_name') or '').strip()
    if vendor.lower() == 'valve' and product.lower() in VALVE_MODELS:
        return VALVE_MODELS[product.lower()]
    for name in (dmi.get('product_family'), dmi.get('product_version')):
        if name and name.strip().lower() not in PLACEHOLDER_DMI and re.search('[A-Za-z]{3}', name):
            return _slug(name)
    return _slug(f'{vendor} {product}') or 'unknown-device'


def os_label(rel: dict) -> str:
    return _slug(f'{rel.get("NAME") or "Linux"} {rel.get("VERSION_ID") or ""}') or 'Linux'


def mode_label(args) -> str:
    sizes = {k: getattr(args, k) for k in DEFAULTS}
    if getattr(args, 'endurance', None):
        return f'endurance-{args.endurance}m' + ('-partial' if args.skip else '')
    if args.skip:
        return 'partial'
    if sizes == QUICK:
        return 'quick'
    return 'full' if sizes == DEFAULTS else 'custom'


def run_name(args, now: datetime.datetime | None = None) -> str:
    """Sortable, comparable folder name: 2026-10-20_124234_Steam-Deck-OLED_SteamOS-3.8.28_quick."""
    now = now or datetime.datetime.now()
    return '_'.join([now.strftime('%Y-%m-%d_%H%M%S'), device_label(sysinfo.dmi()),
                     os_label(sysinfo.os_release()), mode_label(args)])


def default_output(args) -> pathlib.Path:
    return pathlib.Path.home() / 'deck-integrity-reports' / run_name(args)


def interactive() -> bool:
    try:
        return sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


def notify(msg: str) -> None:
    """Best-effort desktop notification, so a double-click is not silently ignored."""
    try:
        runner.run_command(['notify-send', '--app-name=DeckIntegrity',
                            'DeckIntegrity', msg], timeout=5)
    except Exception:
        pass


def preflight(ctx) -> None:
    rec = ctx.rec
    bat = sensors.battery()
    if bat and bat.get('capacity_pct') is not None:
        low = bat['capacity_pct'] < LOW_BATTERY_PCT and (bat.get('status') or '').lower() == 'discharging'
        if low:
            rec.record('Battery', 'WARN', f'{bat["capacity_pct"]}% and discharging. Plug in power: '
                       'a shutdown mid-test leaves an incomplete report.', {'battery': bat},
                       category='Preflight')
        else:
            rec.log(f'Battery: {bat["capacity_pct"]}% ({bat.get("status")})')


def run(args, ctx_factory=Context, listeners=(), control: Control | None = None, echo=print) -> int:
    out = pathlib.Path(args.output).expanduser().resolve() if args.output else default_output(args)
    rec = Recorder(out, Redactor(), echo=echo)
    rec.listeners.extend(listeners)
    rec.args = {k: v for k, v in vars(args).items() if k != 'output'}
    rec.args['mode'] = mode_label(args)
    ctx = ctx_factory(rec=rec, args=args)
    ctx.control = control or Control()
    selected = [n for n in CHECKS if n not in args.skip]
    rec.emit('start', version=VERSION, out=str(out), mode=rec.args['mode'], checks=selected,
             passes=None if args.endurance else args.passes,
             duration_s=args.endurance * 60 if args.endurance else None,
             file_mib=args.file_mib, ram_mib=args.ram_mib)
    info: dict = {}
    rec.log(f'DeckIntegrity {VERSION} | started {rec.started} (UTC)')
    rec.log('Non-destructive: writes only its own temporary test files and reports. No root required.')
    rec.log('Avoid running during large downloads or game installs. Ctrl+C stops cleanly.')
    rec.log('Reports: ' + str(out))
    t0 = time.monotonic()
    try:
        preflight(ctx)
        if 'sysinfo' not in args.skip:
            try:
                info = sysinfo.run(ctx)
            except Exception as e:
                rec.record('System inventory', 'ERROR', repr(e), {'traceback': traceback.format_exc()},
                           category='System inventory')
        for name in args.skip:
            if name in CHECKS:
                rec.log(f'\n(skipping {name} check as requested)')
        preparers = [(name, CHECKS[name]) for name in selected]
        try:
            if args.endurance:
                run_checks(ctx, preparers, duration_s=args.endurance * 60)
            else:
                run_checks(ctx, preparers, passes=args.passes)
        except Exception as e:
            rec.record('Checks', 'ERROR', 'scheduler crashed: ' + repr(e),
                       {'traceback': traceback.format_exc()}, category='setup')
        rec.state = 'completed'
    except KeyboardInterrupt as e:
        rec.state = 'interrupted'
        rec.log(f'\nInterrupted ({e.__class__.__name__} {e}). Writing partial reports.')
    except BaseException:
        rec.state = 'crashed'
        rec.log(traceback.format_exc())
        raise
    finally:
        rec.finished = utc_now()
        if not info:
            try:
                info = sysinfo.collect()
            except Exception:
                info = {}
        reports.write_all(rec, info)
        c = rec.counts()
        rec.section('SUMMARY')
        rec.log(', '.join(f'{k} {v}' for k, v in c.items()) + f' | {time.monotonic() - t0:.0f}s | {rec.state}')
        rec.log('A PASS means no fault was observed in this run; it does not certify the device.')
        rec.log('Review reports for personal identifiers before posting publicly.')
        rec.log('Reports: ' + str(out))
    code = exit_code(rec)
    rec.emit('finish', state=rec.state, counts=rec.counts(), exit_code=code, out=str(out),
             run=rec.run_info)
    return code


def exit_code(rec) -> int:
    c = rec.counts()
    if rec.state == 'interrupted':
        return EXIT_INTERRUPTED
    if c['FAIL']:
        return EXIT_FAIL
    if c['ERROR']:
        return EXIT_ERROR
    return EXIT_OK


def json_lines_writer(control: Control, stream=None):
    """Listener that writes events as JSON lines. If the reader has gone away
    (the GUI closed), stop the run cleanly instead of testing on unattended."""
    def write(msg):
        out = stream or sys.stdout
        try:
            out.write(json.dumps(msg, default=str) + '\n')
            out.flush()
        except (BrokenPipeError, OSError, ValueError):
            control.stop()
    return write


def control_reader(control: Control, stream=None) -> threading.Thread:
    """Read pause/resume/stop commands, one per line. End of input means the
    controlling program has gone away, so stop."""
    def loop():
        for line in (stream or sys.stdin):
            cmd = line.strip().lower()
            if cmd == 'pause':
                control.pause()
            elif cmd == 'resume':
                control.resume()
            elif cmd == 'stop':
                control.stop()
        control.stop()
    t = threading.Thread(target=loop, name='control-reader', daemon=True)
    t.start()
    return t


def main(argv=None) -> int:
    args = parse_args(argv)
    if not args.yes and not interactive():
        print(NOT_CONFIRMED_MSG, file=sys.stderr)
        notify(NOT_CONFIRMED_MSG)
        return EXIT_NOT_CONFIRMED
    install_signal_handlers()
    if args.events:
        control = Control()
        control_reader(control)
        return run(args, listeners=[json_lines_writer(control)], control=control, echo=None)
    return run(args)


def main_exit() -> None:
    """Entry point for the .pyz bundle (zipapp ignores return values)."""
    sys.exit(main())


if __name__ == '__main__':
    main_exit()
