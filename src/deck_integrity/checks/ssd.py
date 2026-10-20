"""Random test file on the internal SSD, read back through several paths.

Per pass, in this order (the order matters for the page cache):
  1. evict cache, sha256sum              - cold buffered read, coreutils
  2. Python, no eviction                 - served from the page cache (RAM) just filled by 1
  3. evict cache, Python                 - cold buffered read, independent implementation
  4. dd iflag=direct | sha256sum         - O_DIRECT, bypasses the page cache
  5. Python O_DIRECT                     - O_DIRECT, independent implementation
The reference hash comes from the data in memory at generation time, never
from reading the file back.
"""
from __future__ import annotations

import pathlib
import shutil
import tempfile
import time

from deck_integrity.checks.common import Method, free_mib, hash_obs, run_methods, tool_obs

CHECK = 'SSD test file'
MARGIN_MIB = 1024
MEANING = ('The file was written once and only read afterwards. Its reference hash was computed '
           'from memory while writing, not by reading it back. Comparing the read paths helps '
           'localise a fault: cold buffered and direct reads come from the SSD, the page-cache '
           'read comes from RAM. A consistent but wrong hash on every path can mean the data was '
           'altered before or while it was written. Inconsistent hashes mean the read or hash '
           'path is unreliable. Neither on its own proves the SSD is defective.')


def run(ctx) -> None:
    rec = ctx.rec
    rec.section(CHECK)
    size = ctx.args.file_mib
    home = pathlib.Path(ctx.home)
    have = free_mib(home)
    if have < size + MARGIN_MIB:
        rec.record(CHECK, 'SKIP', f'need {size + MARGIN_MIB} MiB free in home, have {have} MiB',
                   category=CHECK)
        return
    work = pathlib.Path(tempfile.mkdtemp(prefix='deck-integrity-', dir=str(home)))
    path = work / 'test.bin'
    try:
        t0 = time.monotonic()
        ref = ctx.generate_random_file(path, size)
        rec.record(f'{CHECK}: generate {size} MiB', 'INFO', f'reference sha256={ref.sha256}',
                   {'size_mib': size, 'reference': ref.to_dict()}, category=CHECK, started=t0)
        ok, msg = ctx.evict_cache(path)
        rec.log(f'  Page-cache eviction: {"available" if ok else "NOT available"} ({msg})')
        cold = 'page cache evicted' if ok else 'cache state unknown'
        timeout = ctx.timeout_for(size)

        def evict(obs_fn):
            def wrapped(p):
                ev_ok, ev_msg = ctx.evict_cache(path)
                obs = obs_fn(p)
                obs['evicted'] = ev_ok
                if not ev_ok:
                    obs['evict_note'] = ev_msg
                return obs
            return wrapped

        def coreutils_cold(p):
            return tool_obs(ctx, f'{CHECK}: sha256sum pass {p}',
                            ctx.run_command(['sha256sum', str(path)], timeout))

        def python_buffered(p):
            return hash_obs(ctx.hash_file(path), ref.chunk_hashes)

        def coreutils_direct(p):
            r = ctx.run_pipeline(['dd', 'if=' + str(path), 'iflag=direct', 'bs=4M'],
                                 ['sha256sum'], timeout)
            return tool_obs(ctx, f'{CHECK}: dd direct | sha256sum pass {p}', r, dd_bytes=True)

        def python_direct(p):
            return hash_obs(ctx.hash_file(path, direct=True), ref.chunk_hashes)

        methods = [
            Method(f'buffered read, {cold} (sha256sum)', 'coreutils', evict(coreutils_cold)),
            Method('page-cache read (Python)', 'python', python_buffered),
            Method(f'buffered read, {cold} (Python)', 'python', evict(python_buffered)),
            Method('direct I/O (dd | sha256sum)', 'coreutils', coreutils_direct),
            Method('direct I/O (Python)', 'python', python_direct),
        ]
        run_methods(ctx, CHECK, methods, ctx.args.passes, ref.sha256, ref.bytes, MEANING,
                    {'size_mib': size, 'eviction_available': ok})
    finally:
        if ctx.args.keep_file:
            rec.log('  Test file retained at ' + str(path))
        else:
            shutil.rmtree(work, ignore_errors=True)
            rec.log('  Temporary SSD test files deleted.')
