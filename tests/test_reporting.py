"""Redaction, report files, CLI exit codes and interrupted runs."""
import json
import pathlib
import shutil
import tempfile
import unittest

from deck_integrity import cli, reports
from deck_integrity.checks import zero
from deck_integrity.redact import Redactor
from helpers import FakeTools, make_ctx


class RedactTests(unittest.TestCase):
    def test_home_user_host_serials_macs(self):
        r = Redactor(home='/home/tyler', users=['tyler'], hostname='tylers-deck')
        out = r('path /home/tyler/x by tyler on tylers-deck\nSerial Number: ABC123\n'
                'link aa:bb:cc:dd:ee:ff up')
        for secret in ('/home/tyler', 'tyler', 'ABC123', 'aa:bb:cc'):
            self.assertNotIn(secret, out)
        self.assertIn('$HOME/x', out)

    def test_default_deck_user_left_alone(self):
        r = Redactor(home='/home/deck', users=['deck'], hostname='steamdeck')
        self.assertEqual(r('deck-integrity-reports for deck'), 'deck-integrity-reports for deck')

    def test_hashes_survive(self):
        r = Redactor(home='/home/x', users=[], hostname='')
        self.assertEqual(r(zero.EXPECTED_2GIB), zero.EXPECTED_2GIB)


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_failure_reports_show_expected_and_observed(self):
        ctx = make_ctx(self.tmp, FakeTools({'head': lambda n: 'wrong' if n == 1 else None}))
        zero.run(ctx)
        ctx.rec.state = 'completed'
        reports.write_all(ctx.rec, {'dmi': {'product_name': 'Galileo'}})
        out = ctx.rec.out
        fails = (out / 'failures.txt').read_text()
        self.assertIn(zero.EXPECTED_2GIB, fails)
        self.assertIn('f' * 64, fails)
        summary = (out / 'support-summary.txt').read_text()
        self.assertIn('FAULTS OBSERVED', summary)
        self.assertIn('Galileo', summary)
        self.assertIn('does not by itself identify', summary)
        for name in ('report.txt', 'results.json', 'system-info.json'):
            self.assertTrue((out / name).exists(), name)

    def test_clean_run_says_so(self):
        ctx = make_ctx(self.tmp)
        zero.run(ctx)
        reports.write_all(ctx.rec, {})
        self.assertIn('No failures', (ctx.rec.out / 'failures.txt').read_text())


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, tools, extra=()):
        out = self.tmp / 'reports'
        args = cli.parse_args(['--passes', '2', '--file-mib', '32', '--ram-mib', '32',
                               '--skip', 'sysinfo', '--output', str(out), *extra])

        def factory(rec, args):
            ctx = make_ctx(self.tmp, tools, args=args)
            ctx.rec = rec
            rec.echo = None
            return ctx

        code = cli.run(args, ctx_factory=factory)
        return code, json.loads((out / 'results.json').read_text())

    def test_clean_run_exit_zero(self):
        code, res = self._run(FakeTools())
        self.assertEqual(code, cli.EXIT_OK)
        self.assertEqual(res['state'], 'completed')

    def test_failure_exit_one(self):
        code, _ = self._run(FakeTools({'head': lambda n: 'wrong'}))
        self.assertEqual(code, cli.EXIT_FAIL)

    def test_interrupt_writes_reports_and_cleans(self):
        code, res = self._run(FakeTools({'sha256sum': lambda n: KeyboardInterrupt if n == 3 else None}))
        self.assertEqual(code, cli.EXIT_INTERRUPTED)
        self.assertEqual(res['state'], 'interrupted')
        self.assertTrue((self.tmp / 'reports' / 'support-summary.txt').exists())
        self.assertEqual(list(self.tmp.glob('deck-integrity-*')), [])

    def test_crashing_check_does_not_stop_others(self):
        orig = cli.CHECKS['zero']
        cli.CHECKS['zero'] = lambda ctx: 1 / 0
        try:
            code, res = self._run(FakeTools())
        finally:
            cli.CHECKS['zero'] = orig
        self.assertEqual(code, cli.EXIT_ERROR)
        self.assertTrue(any(t['test'].startswith('SSD') for t in res['tests']))

    def test_quick_preset_and_validation(self):
        a = cli.parse_args(['--quick'])
        self.assertEqual((a.passes, a.file_mib, a.ram_mib), (3, 512, 128))
        with self.assertRaises(SystemExit):
            cli.parse_args(['--passes', '0'])


if __name__ == '__main__':
    unittest.main()
