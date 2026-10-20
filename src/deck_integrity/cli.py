"""Command-line entry point."""
from __future__ import annotations

import argparse
import datetime
import pathlib
import signal
import sys
import time
import traceback

from deck_integrity import VERSION, reports, sensors, sysinfo
from deck_integrity.checks import ram, ssd, zero
from deck_integrity.checks.common import Context
from deck_integrity.recorder import Recorder, utc_now
from deck_integrity.redact import Redactor

CHECKS = {'zero': zero.run, 'ram': ram.run, 'ssd': ssd.run}
EXIT_OK, EXIT_FAIL, EXIT_ERROR, EXIT_INTERRUPTED = 0, 1, 2, 130
LOW_BATTERY_PCT = 20


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
        description='Non-destructive SteamOS/Linux data-integrity diagnostics with logged results.')
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
    a = p.parse_args(argv)
    q = a.quick
    a.passes = a.passes if a.passes is not None else (3 if q else 5)
    a.file_mib = a.file_mib if a.file_mib is not None else (512 if q else 2048)
    a.ram_mib = a.ram_mib if a.ram_mib is not None else (128 if q else 256)
    if not 1 <= a.passes <= 100:
        p.error('--passes must be 1-100')
    if not 32 <= a.file_mib <= 16384:
        p.error('--file-mib must be 32-16384')
    if not 32 <= a.ram_mib <= 4096:
        p.error('--ram-mib must be 32-4096')
    return a


def default_output() -> pathlib.Path:
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    return pathlib.Path.home() / 'deck-integrity-reports' / stamp


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


def run(args, ctx_factory=Context) -> int:
    out = pathlib.Path(args.output).expanduser().resolve() if args.output else default_output()
    rec = Recorder(out, Redactor())
    rec.args = {k: v for k, v in vars(args).items() if k != 'output'}
    ctx = ctx_factory(rec=rec, args=args)
    info: dict = {}
    rec.log(f'Deck Integrity Suite {VERSION} | started {rec.started} (UTC)')
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
        for name, fn in CHECKS.items():
            if name in args.skip:
                rec.log(f'\n(skipping {name} check as requested)')
                continue
            try:
                fn(ctx)
            except Exception as e:
                rec.record(f'{name} check', 'ERROR', 'check crashed: ' + repr(e),
                           {'traceback': traceback.format_exc()}, category=name)
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
    c = rec.counts()
    if rec.state == 'interrupted':
        return EXIT_INTERRUPTED
    if c['FAIL']:
        return EXIT_FAIL
    if c['ERROR']:
        return EXIT_ERROR
    return EXIT_OK


def main(argv=None) -> int:
    args = parse_args(argv)
    install_signal_handlers()
    return run(args)


def main_exit() -> None:
    """Entry point for the .pyz bundle (zipapp ignores return values)."""
    sys.exit(main())


if __name__ == '__main__':
    main_exit()
