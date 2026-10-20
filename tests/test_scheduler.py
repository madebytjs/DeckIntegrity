"""Endurance scheduling, pause/stop control and the GUI event stream."""
import io
import json
import os
import pathlib
import shutil
import tempfile
import threading
import time
import unittest

from deck_integrity import cli, reports
from deck_integrity.checks import ram, ssd, zero
from deck_integrity.scheduler import Control, Stopped, run_checks
from helpers import FakeTools, make_ctx


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.events = []

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def ctx(self, tools=None):
        ctx = make_ctx(self.tmp, tools)
        ctx.rec.listeners.append(self.events.append)
        ctx.control = Control()
        return ctx

    def kinds(self, kind):
        return [e for e in self.events if e['event'] == kind]


class EnduranceTests(Base):
    def test_runs_rounds_until_time_is_up_and_writes_file_once(self):
        ctx = self.ctx()
        writes = []
        real = ctx.generate_random_file
        ctx.generate_random_file = lambda p, s: writes.append(p) or real(p, s)
        run_checks(ctx, [zero.prepare, ram.prepare, ssd.prepare], duration_s=0.5)
        sched = ctx.rec.run_info['scheduler']
        self.assertEqual(sched['mode'], 'endurance')
        self.assertGreaterEqual(sched['rounds'], 2)
        self.assertEqual(len(writes), 2)   # RAM + SSD files created once, however many rounds
        self.assertTrue(all(r['status'] in ('PASS', 'INFO', 'SKIP') for r in ctx.rec.results))
        self.assertEqual(list(self.tmp.glob('deck-integrity-*')), [])

    def test_first_failure_and_failure_times_recorded(self):
        tools = FakeTools({'head': lambda n: 'wrong' if n in (2, 4) else None})
        ctx = self.ctx(tools)
        run_checks(ctx, [zero.prepare], passes=5)
        sched = ctx.rec.run_info['scheduler']
        self.assertEqual(sched['first_failure']['pass'], 2)
        self.assertEqual(sched['first_failure']['method'], 'head -c | sha256sum')
        self.assertEqual(len(sched['failure_times_s']), 2)
        statuses = [e['status'] for e in self.kinds('pass') if e['method'] == 'head -c | sha256sum']
        self.assertEqual(statuses, ['ok', 'mismatch', 'ok', 'mismatch', 'ok'])

    def test_endurance_summary_reports_first_failure_timing(self):
        ctx = self.ctx(FakeTools({'dd': lambda n: 'wrong' if n == 2 else None}))
        run_checks(ctx, [zero.prepare], duration_s=0.3)
        reports.write_all(ctx.rec, {})
        text = (ctx.rec.out / 'support-summary.txt').read_text()
        self.assertIn('ENDURANCE RUN: planned 0.005 min', text)
        self.assertIn('First failure: 2 GiB /dev/zero stream / dd | sha256sum, pass 2', text)
        self.assertIn('Failed passes: 1', text)

    def test_progress_reaches_one(self):
        ctx = self.ctx()
        run_checks(ctx, [zero.prepare], passes=3)
        fracs = [e['fraction'] for e in self.kinds('progress')]
        self.assertEqual(fracs[-1], 1.0)
        self.assertEqual(fracs, sorted(fracs))

    def test_live_results_saved_each_round_then_cleared(self):
        ctx = self.ctx()
        seen = []
        orig = ctx.rec.save
        ctx.rec.save = lambda: (seen.append(ctx.rec.live is not None), orig())
        run_checks(ctx, [zero.prepare], passes=2)
        self.assertTrue(any(seen))
        self.assertIsNone(json.loads((ctx.rec.out / 'results.json').read_text())['live'])


class ControlTests(Base):
    def test_stop_records_partial_results_and_cleans_up(self):
        ctx = self.ctx(FakeTools({'sha256sum': lambda n: (ctx.control.stop(), None)[1] if n == 2 else None}))
        with self.assertRaises(Stopped):
            run_checks(ctx, [ssd.prepare], passes=5)
        self.assertTrue(ctx.rec.run_info['scheduler']['interrupted'])
        self.assertTrue(all(r['status'] in ('WARN', 'INFO', 'SKIP') for r in ctx.rec.results))
        self.assertEqual(list(self.tmp.glob('deck-integrity-*')), [])

    def test_pause_blocks_then_resume_continues(self):
        ctx = self.ctx()
        ctx.control.pause()
        t = threading.Thread(target=run_checks, args=(ctx, [zero.prepare]), kwargs={'passes': 1})
        t.start()
        time.sleep(0.3)
        self.assertEqual(self.kinds('pass'), [])
        self.assertTrue(self.kinds('paused'))
        ctx.control.resume()
        t.join(5)
        self.assertFalse(t.is_alive())
        self.assertEqual(len(self.kinds('pass')), 3)
        self.assertGreaterEqual(ctx.rec.run_info['scheduler']['paused_s'], 0.2)

    def test_control_reader_commands_then_eof_stops(self):
        rfd, wfd = os.pipe()
        reader, writer = os.fdopen(rfd, 'r'), os.fdopen(wfd, 'w')
        c = Control()
        t = cli.control_reader(c, reader)

        def send(cmd, expect_paused):
            writer.write(cmd + '\n')
            writer.flush()
            deadline = time.monotonic() + 2
            while c.paused != expect_paused and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(c.paused, expect_paused, cmd)

        send('pause', True)
        send('resume', False)
        self.assertFalse(c.stop_requested)
        writer.close()                       # GUI went away
        t.join(2)
        self.assertTrue(c.stop_requested)

    def test_broken_event_pipe_stops_run(self):
        c = Control()

        class Closed(io.StringIO):
            def write(self, s):
                raise BrokenPipeError

        cli.json_lines_writer(c, Closed())({'event': 'log'})
        self.assertTrue(c.stop_requested)


class EventsCliTests(Base):
    def test_events_stream_is_json_lines_with_start_and_finish(self):
        out = io.StringIO()
        control = Control()
        args = cli.parse_args(['--passes', '1', '--file-mib', '32', '--ram-mib', '32',
                               '--skip', 'sysinfo', '--output', str(self.tmp / 'r')])

        def factory(rec, args):
            ctx = make_ctx(self.tmp, None, args=args)
            ctx.rec = rec
            return ctx

        code = cli.run(args, ctx_factory=factory, listeners=[cli.json_lines_writer(control, out)],
                       control=control, echo=None)
        lines = [json.loads(x) for x in out.getvalue().splitlines()]
        kinds = [x['event'] for x in lines]
        self.assertEqual(kinds[0], 'start')
        self.assertEqual(kinds[-1], 'finish')
        self.assertEqual(lines[-1]['exit_code'], code)
        self.assertIn('pass', kinds)
        self.assertIn('result', kinds)

    def test_endurance_args(self):
        a = cli.parse_args(['--endurance', '60'])
        self.assertEqual(cli.mode_label(a), 'endurance-60m')
        with self.assertRaises(SystemExit):
            cli.parse_args(['--endurance', '0'])


if __name__ == '__main__':
    unittest.main()
