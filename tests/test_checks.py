"""Checks driven end to end with simulated faults."""
import pathlib
import shutil
import tempfile
import unittest

from deck_integrity import hashing
from deck_integrity.checks import ram, ssd, zero
from helpers import FakeTools, by_test, make_args, make_ctx


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def status(self, ctx, pattern):
        rows = by_test(ctx.rec, pattern)
        self.assertEqual(len(rows), 1, [r['test'] for r in ctx.rec.results])
        return rows[0]

    def leftovers(self):
        return list(self.tmp.glob('deck-integrity-*')) + list((self.tmp / 'shm').glob('deck-integrity-*'))


class ZeroTests(Base):
    def test_healthy(self):
        ctx = make_ctx(self.tmp)
        zero.run(ctx)
        self.assertEqual({r['status'] for r in ctx.rec.results}, {'PASS'})
        self.assertEqual(len(ctx.rec.results), 3)

    def test_intermittent_fault_in_one_tool(self):
        tools = FakeTools({'head': lambda n: 'wrong' if n == 2 else None})
        ctx = make_ctx(self.tmp, tools)
        zero.run(ctx)
        r = self.status(ctx, r'head')
        self.assertEqual(r['status'], 'FAIL')
        self.assertEqual(r['data']['stats']['passes_wrong_hash'], 1)
        self.assertEqual([o['sha256'] == zero.EXPECTED_2GIB for o in r['data']['observations']],
                         [True, False, True])
        self.assertEqual(self.status(ctx, r'dd \|')['status'], 'PASS')

    def test_short_stream_detected_by_dd_byte_count(self):
        ctx = make_ctx(self.tmp, FakeTools({'dd': lambda n: 'short'}))
        zero.run(ctx)
        self.assertEqual(self.status(ctx, r'dd \|')['status'], 'FAIL')

    def test_python_path_fault(self):
        ctx = make_ctx(self.tmp)
        ctx.hash_device_stream = lambda p, t: hashing.HashResult('0' * 64, t, 0.1)
        zero.run(ctx)
        self.assertEqual(self.status(ctx, r'Python')['status'], 'FAIL')

    def test_timeout_does_not_abort_other_methods(self):
        ctx = make_ctx(self.tmp, FakeTools({'head': lambda n: 'timeout'}))
        zero.run(ctx)
        self.assertEqual(self.status(ctx, r'head')['status'], 'ERROR')
        self.assertEqual(self.status(ctx, r'Python')['status'], 'PASS')

    def test_missing_tool_is_skip(self):
        ctx = make_ctx(self.tmp, FakeTools({'head': lambda n: 'missing'}))
        zero.run(ctx)
        self.assertEqual(self.status(ctx, r'head')['status'], 'SKIP')

    def test_interrupt_records_partial_results(self):
        tools = FakeTools({'dd': lambda n: KeyboardInterrupt if n == 2 else None})
        ctx = make_ctx(self.tmp, tools)
        with self.assertRaises(KeyboardInterrupt):
            zero.run(ctx)
        r = self.status(ctx, r'head')
        self.assertEqual(r['status'], 'WARN')
        self.assertTrue(r['data']['interrupted'])
        self.assertEqual(len(r['data']['observations']), 2)
        self.assertTrue((ctx.rec.out / 'results.json').exists())


class RamTests(Base):
    def test_healthy_and_cleaned_up(self):
        ctx = make_ctx(self.tmp)
        ram.run(ctx)
        self.assertEqual(self.status(ctx, r'sha256sum')['status'], 'PASS')
        self.assertEqual(self.status(ctx, r'Python')['status'], 'PASS')
        self.assertEqual(self.leftovers(), [])

    def test_flipped_read_fails(self):
        ctx = make_ctx(self.tmp, FakeTools({'sha256sum': lambda n: 'flip' if n == 3 else None}))
        ram.run(ctx)
        self.assertEqual(self.status(ctx, r'sha256sum')['status'], 'FAIL')

    def test_skip_when_shm_missing(self):
        ctx = make_ctx(self.tmp)
        ctx.shm = self.tmp / 'nope'
        ram.run(ctx)
        self.assertEqual(ctx.rec.results[0]['status'], 'SKIP')


class SsdTests(Base):
    def test_healthy(self):
        ctx = make_ctx(self.tmp)
        ssd.run(ctx)
        statuses = {r['test']: r['status'] for r in ctx.rec.results}
        self.assertNotIn('FAIL', statuses.values(), statuses)
        self.assertNotIn('ERROR', statuses.values(), statuses)
        self.assertEqual(self.leftovers(), [])

    def test_corrupted_python_read_reports_chunk(self):
        ctx = make_ctx(self.tmp)
        real = hashing.hash_file
        calls = {'n': 0}

        def flaky(path, direct=False, chunk_size=hashing.CHUNK):
            calls['n'] += 1
            res = real(path, direct=direct, chunk_size=chunk_size)
            if calls['n'] == 4 and not direct:   # pass 2, first buffered Python read
                res.sha256 = 'e' * 64
                res.chunk_hashes = ['0' * 64] + res.chunk_hashes[1:]
            return res

        ctx.hash_file = flaky
        ssd.run(ctx)
        r = self.status(ctx, r'page-cache read')
        self.assertEqual(r['status'], 'FAIL')
        bad = [o for o in r['data']['observations'] if o.get('chunk_mismatches')]
        self.assertEqual(bad[0]['chunk_mismatches'], [{'index': 0, 'offset_mib': 0}])

    def test_direct_dd_fault(self):
        ctx = make_ctx(self.tmp, FakeTools({'dd-direct': lambda n: 'flip'}))
        ssd.run(ctx)
        self.assertEqual(self.status(ctx, r'direct I/O \(dd')['status'], 'FAIL')

    def test_insufficient_space_skips(self):
        ctx = make_ctx(self.tmp, args=make_args(file_mib=16384))
        import deck_integrity.checks.ssd as mod
        orig = mod.free_mib
        mod.free_mib = lambda p: 100
        try:
            ssd.run(ctx)
        finally:
            mod.free_mib = orig
        self.assertEqual(ctx.rec.results[0]['status'], 'SKIP')

    def test_interrupt_cleans_up_test_file(self):
        ctx = make_ctx(self.tmp, FakeTools({'sha256sum': lambda n: KeyboardInterrupt if n == 2 else None}))
        with self.assertRaises(KeyboardInterrupt):
            ssd.run(ctx)
        self.assertEqual(self.leftovers(), [])

    def test_keep_file(self):
        ctx = make_ctx(self.tmp, args=make_args(keep_file=True, passes=1))
        ssd.run(ctx)
        self.assertEqual(len(list(self.tmp.glob('deck-integrity-*/test.bin'))), 1)

    def test_generation_failure_cleans_up(self):
        ctx = make_ctx(self.tmp)

        def boom(path, size):
            pathlib.Path(path).write_bytes(b'partial')
            raise OSError('No space left on device')

        ctx.generate_random_file = boom
        with self.assertRaises(OSError):
            ssd.run(ctx)
        self.assertEqual(self.leftovers(), [])


if __name__ == '__main__':
    unittest.main()
