"""Inventory command classification, using outputs seen on a Legion Go S (SteamOS 3.8)."""
import pathlib
import shutil
import tempfile
import unittest

from deck_integrity import sysinfo
from deck_integrity.runner import CommandResult
from helpers import by_test, make_ctx

OUTPUTS = {
    'lspci': (0, '00:00.0 Host bridge [0600]: AMD Device [1022:1507]\n',
              'pcilib: Error reading /sys/bus/pci/devices/0000:00:08.3/label: Operation not permitted\n'),
    'dmesg': (1, '', 'dmesg: read kernel buffer failed: Operation not permitted\n'),
    'journalctl': (1, '', 'No journal files were opened due to insufficient permissions.\n'),
}


def fake_run(argv, timeout):
    code, out, err = OUTPUTS.get(argv[0], (0, 'ok\n', ''))
    return CommandResult(argv=argv, returncode=code, stdout=out, stderr=err, elapsed=0.01)


class SysinfoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.ctx = make_ctx(self.tmp)
        self.ctx.run_command = fake_run
        sysinfo.run(self.ctx)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def status(self, pattern):
        return by_test(self.ctx.rec, pattern)[0]['status']

    def test_partial_permission_noise_is_not_a_warning(self):
        self.assertEqual(self.status('PCI devices'), 'INFO')

    def test_restricted_dmesg_is_skipped_not_warned(self):
        self.assertEqual(self.status('ring buffer'), 'SKIP')

    def test_unreadable_journal_still_warns(self):
        self.assertEqual(self.status('Kernel log warnings'), 'WARN')


if __name__ == '__main__':
    unittest.main()
