"""GUI tests: run headless (QT_QPA_PLATFORM=offscreen). Skipped if PySide6 is absent."""
import json
import os
import pathlib
import shutil
import tempfile
import unittest
from unittest import mock

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
try:
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtWidgets import QApplication, QMessageBox
    HAVE_QT = True
except ImportError:   # core tests must still run without the GUI toolkit
    HAVE_QT = False

SMALL = {'mode': 'quick', 'endurance_min': None, 'passes': 1, 'file_mib': 32, 'ram_mib': 32,
         'skip': ['sysinfo'], 'keep_file': False}


def app():
    return QApplication.instance() or QApplication([])


def wait_for(signal, timeout_ms):
    loop = QEventLoop()
    signal.connect(lambda *a: loop.quit())
    QTimer.singleShot(timeout_ms, loop.quit)
    loop.exec()


@unittest.skipUnless(HAVE_QT, 'PySide6 not installed')
class EngineUnitTests(unittest.TestCase):
    def test_command_for_endurance_and_passes(self):
        from deck_integrity.gui.engine import engine_command
        e = engine_command({**SMALL, 'endurance_min': 60, 'skip': ['ram'], 'keep_file': True})
        self.assertIn('--events', e)
        self.assertIn('--yes', e)
        self.assertEqual(e[e.index('--endurance') + 1], '60')
        self.assertNotIn('--passes', e)
        self.assertEqual(e[e.index('--skip') + 1], 'ram')
        self.assertIn('--keep-file', e)
        self.assertEqual(engine_command(SMALL)[engine_command(SMALL).index('--passes') + 1], '1')

    def test_interpreter_prefers_bundled_python_inside_appimage(self):
        # Regression: in the AppImage, sys.executable relaunched the GUI instead of the engine.
        import sys
        from deck_integrity.gui.engine import interpreter
        tmp = pathlib.Path(tempfile.mkdtemp())
        try:
            (tmp / 'AppRun.python').write_text('')
            with mock.patch.dict(os.environ, {'APPDIR': str(tmp)}):
                self.assertEqual(interpreter(), [str(tmp / 'AppRun.python'), '-I'])
            with mock.patch.dict(os.environ, {'APPDIR': ''}):
                self.assertEqual(interpreter(), [sys.executable])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_parse_lines_keeps_partial_tail(self):
        from deck_integrity.gui.engine import parse_lines
        events, rest, bad = parse_lines(b'{"event":"a"}\nnot json\n{"event":"b"}\n{"event":"c')
        self.assertEqual([e['event'] for e in events], ['a', 'b'])
        self.assertEqual(rest, b'{"event":"c')
        self.assertEqual(bad, ['not json'])


@unittest.skipUnless(HAVE_QT, 'PySide6 not installed')
class RunPageTests(unittest.TestCase):
    def setUp(self):
        app()
        from deck_integrity.gui.run_page import RunPage
        self.page = RunPage()

    def test_live_counts_and_final_verdict(self):
        p = self.page
        for r, status in enumerate(['ok', 'mismatch', 'ok'], 1):
            p.on_event({'event': 'pass', 'check': 'Z', 'method': 'm', 'round': r, 'status': status,
                        'at_s': r, 'max_temp_c': 60 + r})
        row = p.table.rows[('Z', 'm')]
        self.assertEqual((row['passes'], row['faults']), (3, 1))
        self.assertEqual(len(p.timeline.ticks), 3)
        self.assertIn('peak 63', p.temp.text())
        p.on_event({'event': 'result', 'test': 'Z: m', 'status': 'FAIL'})
        p.on_event({'event': 'finish', 'state': 'completed', 'counts': {'FAIL': 1}, 'out': '/x'})
        self.assertEqual(row['status'], 'FAIL')
        self.assertEqual(p.status.text(), 'Problems found: data did not match')
        self.assertTrue(p.back_btn.isVisibleTo(p))
        self.assertFalse(p.stop_btn.isVisibleTo(p))

    def test_redacted_report_path_is_resolved_for_open_and_copy(self):
        # Regression: 'Open report folder' tried to open a literal '$HOME/...' path.
        home = pathlib.Path.home()
        self.page.on_event({'event': 'start', 'out': '$HOME/deck-integrity-reports/run1', 'mode': 'quick'})
        self.assertEqual(pathlib.Path(self.page.out), home / 'deck-integrity-reports' / 'run1')
        self.page.on_event({'event': 'finish', 'state': 'completed', 'counts': {},
                            'out': '$HOME/deck-integrity-reports/run1'})
        self.assertEqual(pathlib.Path(self.page.out), home / 'deck-integrity-reports' / 'run1')
        from deck_integrity.gui.run_page import local_path
        self.assertEqual(local_path('/abs/other'), '/abs/other')
        self.assertIsNone(local_path(None))

    def test_crash_without_finish_is_reported(self):
        self.page.show_crash(1, ['Traceback ...', 'Boom'])
        self.assertIn('stopped unexpectedly', self.page.status.text())
        self.assertIn('Boom', self.page.log.toPlainText())


def isolated_settings(tmp: pathlib.Path) -> None:
    from PySide6.QtCore import QSettings
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(tmp / 'settings'))


@unittest.skipUnless(HAVE_QT, 'PySide6 not installed')
class DisclaimerTests(unittest.TestCase):
    def setUp(self):
        app()
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        isolated_settings(self.tmp)
        from deck_integrity.gui.window import MainWindow
        self.win = MainWindow()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_cancel_blocks_the_test_and_is_not_remembered(self):
        from PySide6.QtCore import QSettings
        with mock.patch.object(QMessageBox, 'exec', return_value=0), \
                mock.patch.object(QMessageBox, 'clickedButton', return_value=None), \
                mock.patch.object(self.win.engine, 'start') as start:
            self.win.start(SMALL)
        start.assert_not_called()
        s = QSettings(QSettings.IniFormat, QSettings.UserScope, 'DeckIntegrity', 'DeckIntegrity')
        self.assertEqual(s.value('disclaimer_accepted', ''), '')

    def test_accept_runs_and_is_remembered(self):
        shown = []

        def click_agree(box):
            shown.append(box.informativeText())
            agree = next(b for b in box.buttons() if b.text().startswith('I understand'))
            box.clickedButton = lambda: agree
            return 0

        with mock.patch.object(QMessageBox, 'exec', click_agree), \
                mock.patch.object(self.win.engine, 'start') as start:
            self.win.start(SMALL)
            self.win.start(SMALL)   # second time: no dialog
        self.assertEqual(start.call_count, 2)
        self.assertEqual(len(shown), 1)
        self.assertIn('at your own risk', shown[0])


@unittest.skipUnless(HAVE_QT, 'PySide6 not installed')
class WindowIntegrationTests(unittest.TestCase):
    """Drive the real engine as a child process, exactly as the app does."""

    def setUp(self):
        app()
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        isolated_settings(self.tmp)
        from deck_integrity.gui.window import MainWindow
        self.win = MainWindow()
        self.win.accept_disclaimer = lambda: True

    def tearDown(self):
        if self.win.engine.running:
            self.win.engine.kill()
            wait_for(self.win.engine.ended, 5000)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_quick_run_end_to_end(self):
        self.win.start({**SMALL, 'output': str(self.tmp / 'r')})
        wait_for(self.win.engine.ended, 120000)
        page = self.win.run_page
        self.assertTrue(page.finished, page.log.toPlainText()[-2000:])
        self.assertEqual(page.status.text(), 'No problems found')
        self.assertTrue(page.table.rows)
        res = json.loads((self.tmp / 'r' / 'results.json').read_text())
        self.assertEqual(res['state'], 'completed')

    def test_wrong_program_is_stopped_by_watchdog_not_left_hanging(self):
        # Simulates the AppImage bug: something starts but never speaks the engine protocol.
        import sys
        from deck_integrity.gui import engine as engine_mod
        silent = [sys.executable, '-c', 'import time; time.sleep(60)']
        with mock.patch.object(engine_mod, 'interpreter', return_value=silent), \
                mock.patch.object(engine_mod, 'START_TIMEOUT_MS', 1500):
            self.win.start({**SMALL, 'output': str(self.tmp / 'r')})
            wait_for(self.win.engine.ended, 20000)
        self.assertFalse(self.win.engine.running)
        self.assertTrue(self.win.engine.failed_to_start)
        self.assertEqual(self.win.run_page.status.text(), 'The test engine did not start')
        self.assertIn('Starting engine:', self.win.run_page.log.toPlainText())

    def test_closing_mid_run_stops_cleanly_and_saves(self):
        opts = {**SMALL, 'mode': 'endurance', 'endurance_min': 5, 'output': str(self.tmp / 'r')}
        self.win.start(opts)
        wait_for(self.win.engine.event, 30000)   # engine is up
        loop = QEventLoop()
        self.win.engine.event.connect(lambda e: e.get('event') == 'pass' and loop.quit())
        QTimer.singleShot(60000, loop.quit)
        loop.exec()
        with mock.patch.object(QMessageBox, 'question', return_value=QMessageBox.Yes):
            self.win.close()
        wait_for(self.win.engine.ended, 30000)
        self.assertFalse(self.win.engine.running)
        res = json.loads((self.tmp / 'r' / 'results.json').read_text())
        self.assertEqual(res['state'], 'interrupted')
        self.assertEqual(list(pathlib.Path.home().glob('deck-integrity-*/test.bin')), [])


if __name__ == '__main__':
    unittest.main()
