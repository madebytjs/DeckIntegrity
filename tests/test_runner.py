"""Real subprocess behaviour: needs coreutils on PATH (Linux, or Git Bash on Windows)."""
import hashlib
import shutil
import time
import unittest

from deck_integrity import runner

HAVE = all(shutil.which(t) for t in ('head', 'sha256sum', 'sleep', 'dd'))


@unittest.skipUnless(HAVE, 'coreutils not on PATH')
class RunnerTests(unittest.TestCase):
    def test_pipeline_hashes_zero_stream(self):
        r = runner.run_pipeline(['head', '-c', '1048576', '/dev/zero'], ['sha256sum'], 30)
        self.assertTrue(r.ok, r.to_dict())
        self.assertEqual(r.consumer.stdout.split()[0], hashlib.sha256(bytes(1048576)).hexdigest())

    def test_dd_reports_byte_count_on_stderr(self):
        r = runner.run_pipeline(['dd', 'if=/dev/zero', 'bs=64k', 'count=4', 'iflag=fullblock'],
                                ['sha256sum'], 30)
        self.assertTrue(r.ok, r.to_dict())
        self.assertIn('262144 bytes', r.producer.stderr)

    def test_command_timeout_kills_process(self):
        t0 = time.monotonic()
        r = runner.run_command(['sleep', '20'], timeout=0.5)
        self.assertTrue(r.timed_out)
        self.assertFalse(r.ok)
        self.assertLess(time.monotonic() - t0, 10)

    def test_pipeline_timeout_kills_both_stages(self):
        t0 = time.monotonic()
        r = runner.run_pipeline(['sleep', '20'], ['sha256sum'], timeout=0.5)
        self.assertTrue(r.timed_out)
        self.assertFalse(r.ok)
        self.assertLess(time.monotonic() - t0, 10)

    def test_nonzero_exit_captured_with_stderr(self):
        r = runner.run_command(['sha256sum', '/definitely/not/here'], timeout=10)
        self.assertNotEqual(r.returncode, 0)
        self.assertTrue(r.stderr)


class MissingToolTests(unittest.TestCase):
    def test_missing_command(self):
        r = runner.run_command(['no-such-tool-xyz'], timeout=5)
        self.assertTrue(r.missing)

    def test_missing_pipeline_stage(self):
        r = runner.run_pipeline(['no-such-tool-xyz'], ['sha256sum'], 5)
        self.assertTrue(r.missing)
        self.assertFalse(r.ok)


if __name__ == '__main__':
    unittest.main()
