"""Random data held in a RAM-backed file (/dev/shm), hashed repeatedly."""
from __future__ import annotations

import os
import pathlib
import tempfile
import time

from deck_integrity import sensors
from deck_integrity.checks.common import (CheckPlan, Method, free_mib, hash_obs, tool_obs,
                                          writable_dir)
from deck_integrity.scheduler import run_checks

CHECK = 'RAM-backed file'
MEANING = ('The test file lives in RAM (/dev/shm), so these reads do not touch the SSD. The '
           'reference hash was computed from the data in memory as it was generated. A mismatch '
           'means the stored data or the read/hash path changed it. This is a spot check, not an '
           'exhaustive memory test such as Memtest86.')


def prepare(ctx) -> CheckPlan | None:
    rec = ctx.rec
    rec.section(CHECK + ' (/dev/shm)')
    shm = pathlib.Path(ctx.shm)
    if not writable_dir(shm):
        rec.record(CHECK, 'SKIP', f'{shm} unavailable or not writable', category=CHECK)
        return None
    avail_mib = sensors.meminfo().get('MemAvailable', 0) // 1024
    limits = [ctx.args.ram_mib, free_mib(shm) - 64]
    if avail_mib:
        limits.append(avail_mib // 4)   # never take more than a quarter of available RAM
    size = min(limits)
    if size < 32:
        rec.record(CHECK, 'SKIP', f'insufficient RAM-backed space (limits {limits} MiB)', category=CHECK)
        return None
    if size < ctx.args.ram_mib:
        rec.log(f'  Reduced RAM test size to {size} MiB to stay within safe limits.')
    fd, name = tempfile.mkstemp(prefix='deck-integrity-', suffix='.bin', dir=str(shm))
    os.close(fd)
    path = pathlib.Path(name)

    def cleanup():
        path.unlink(missing_ok=True)

    try:
        t0 = time.monotonic()
        ref = ctx.generate_random_file(path, size)
    except BaseException:
        cleanup()
        raise
    rec.record(f'{CHECK}: generate {size} MiB', 'INFO', f'reference sha256={ref.sha256}',
               {'size_mib': size, 'reference': ref.to_dict()}, category=CHECK, started=t0)
    timeout = ctx.timeout_for(size)

    def coreutils(p):
        return tool_obs(ctx, f'{CHECK}: sha256sum pass {p}',
                        ctx.run_command(['sha256sum', str(path)], timeout))

    def python(p):
        return hash_obs(ctx.hash_file(path), ref.chunk_hashes)

    methods = [Method('sha256sum', 'coreutils', coreutils),
               Method('Python hashlib', 'python', python)]
    return CheckPlan(CHECK, methods, ref.sha256, ref.bytes, MEANING, {'size_mib': size}, cleanup)


def run(ctx) -> None:
    """Run this check on its own (tests, scripting)."""
    run_checks(ctx, [prepare], passes=ctx.args.passes)
