"""2 GiB /dev/zero stream: the check that never touches storage."""
from __future__ import annotations

import os

from deck_integrity.checks.common import Method, hash_obs, run_methods, tool_obs
from deck_integrity.hashing import GIB

# SHA-256 of exactly 2 GiB (2147483648 bytes) of 0x00. A published constant,
# deliberately not computed at run time on the machine under test.
EXPECTED_2GIB = 'a7c744c13cc101ed66c29f672f92455547889cc586ce6d44fe76ae824958ea51'
SIZE = 2 * GIB
CHECK = '2 GiB /dev/zero stream'
MEANING = ('The zero stream is produced by the kernel in memory and never read from storage. '
           'An incorrect or inconsistent hash means the data was altered somewhere in the '
           'generate, pipe or hash path (CPU, RAM, kernel or the tools involved). On its own it '
           'does not identify which component is at fault. If only one implementation fails, '
           'that points more towards that tool or code path than towards general corruption.')


def run(ctx) -> None:
    rec = ctx.rec
    rec.section(CHECK)
    dev = ctx.zero_device
    if not os.path.exists(dev):
        rec.record(CHECK, 'SKIP', dev + ' not present', category=CHECK)
        return
    timeout = ctx.timeout_for(SIZE // (1024 ** 2))

    def head_sha(p):
        # The exact command that first exposed the fault in October 2026.
        r = ctx.run_pipeline(['head', '-c', str(SIZE), dev], ['sha256sum'], timeout)
        return tool_obs(ctx, f'{CHECK}: head | sha256sum pass {p}', r)

    def dd_sha(p):
        # dd reports its byte count on stderr, giving an independent length check.
        r = ctx.run_pipeline(['dd', 'if=' + dev, 'bs=4M', 'count=512', 'iflag=fullblock'],
                             ['sha256sum'], timeout)
        return tool_obs(ctx, f'{CHECK}: dd | sha256sum pass {p}', r, dd_bytes=True)

    def python(p):
        return hash_obs(ctx.hash_device_stream(dev, SIZE))

    methods = [
        Method('head -c | sha256sum', 'coreutils', head_sha),
        Method('dd | sha256sum', 'coreutils', dd_sha),
        Method('Python hashlib', 'python', python),
    ]
    run_methods(ctx, CHECK, methods, ctx.args.passes, EXPECTED_2GIB, SIZE, MEANING)
