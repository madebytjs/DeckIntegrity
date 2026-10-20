"""Run screen: live progress, pass timeline, results, pause/stop, then the verdict."""
from __future__ import annotations

import pathlib

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QPlainTextEdit, QProgressBar, QPushButton, QSizePolicy,
                               QVBoxLayout, QWidget)

from deck_integrity import history
from deck_integrity.gui import theme
from deck_integrity.gui.setup_page import label, panel
from deck_integrity.gui.widgets import HealthCards, ResultsTable, Timeline

VERDICTS = {
    'clean': ('No problems found', 'Your storage, memory and processor handled every test correctly. '
              'That is a good sign, but one run cannot prove a device is healthy: problems that come '
              'and go can be missed, so an Endurance run is worth doing.'),
    'faults': ('Problems found: data did not match', 'Some data came back different from what was '
               'written or expected. That points to a hardware or system fault, but not to which part. '
               "The support summary lists what failed and when; share it with your device maker's "
               'support.'),
    'errors': ('Some checks could not finish', 'No data mismatches were seen, but some checks could '
               'not complete. See the log or report for details.'),
    'stopped': ('Stopped early', 'Results up to the stop were saved.'),
    'unknown': ('Finished', ''),
}


def local_path(path: str | None) -> str | None:
    """Undo report redaction for local use: the engine writes '$HOME/...' so shared
    reports do not reveal a username, but the app must open the real folder."""
    if path == '$HOME':
        return str(pathlib.Path.home())
    if path and path[:6] in ('$HOME/', '$HOME\\'):
        return str(pathlib.Path.home() / path[6:])
    return path


def running_title(mode: str) -> str:
    """'endurance-60m' -> 'Running endurance test (60 min)', 'quick' -> 'Running quick test'."""
    if mode.startswith('endurance-'):
        minutes = mode.split('-')[1].rstrip('m')
        return f'Running endurance test ({minutes} min)'
    return f'Running {mode.split("-")[0] or "a"} test'


def hms(seconds) -> str:
    s = int(seconds or 0)
    return f'{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}' if s >= 3600 else f'{s // 60}:{s % 60:02d}'


class RunPage(QWidget):
    pause_requested = Signal()
    resume_requested = Signal()
    stop_requested = Signal()
    back_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setSpacing(12)

        top = panel()
        top.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)   # never squash the verdict
        t = QVBoxLayout(top)
        t.setContentsMargins(18, 14, 18, 14)
        row = QHBoxLayout()
        self.status = label('Starting...', 'big')
        row.addWidget(self.status, 1)
        self.temp = label('', 'muted')
        row.addWidget(self.temp)
        t.addLayout(row)
        self.detail = label('', 'muted', wrap=True)
        # Wrapped text does not report its height to the layout; reserve room for the verdict.
        self.detail.setMinimumHeight(68)   # three lines at the 15px theme font
        t.addWidget(self.detail)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        t.addWidget(self.bar)
        row2 = QHBoxLayout()
        self.activity = label('', 'muted')
        row2.addWidget(self.activity, 1)
        self.times = label('', 'muted')
        row2.addWidget(self.times)
        t.addLayout(row2)
        v.addWidget(top)

        self.health = HealthCards()
        v.addWidget(self.health)
        self.timeline = Timeline()
        v.addWidget(self.timeline)
        self.table = ResultsTable()
        self.table.setMinimumHeight(120)
        v.addWidget(self.table, 1)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(5000)
        self.log.setVisible(False)
        v.addWidget(self.log, 1)

        buttons = QHBoxLayout()
        self.log_btn = QPushButton('Show log')
        self.log_btn.setCheckable(True)
        self.log_btn.toggled.connect(self._toggle_log)
        buttons.addWidget(self.log_btn)
        buttons.addStretch()
        self.pause_btn = QPushButton('Pause')
        self.pause_btn.clicked.connect(self._pause_clicked)
        self.stop_btn = QPushButton('Stop')
        self.stop_btn.setObjectName('danger')
        self.stop_btn.clicked.connect(self.stop_requested)
        self.open_btn = QPushButton('Open report folder')
        self.open_btn.clicked.connect(self._open_folder)
        self.copy_btn = QPushButton('Copy support summary')
        self.copy_btn.clicked.connect(self._copy_summary)
        self.back_btn = QPushButton('New test')
        self.back_btn.setObjectName('primary')
        self.back_btn.clicked.connect(self.back_requested)
        for b in (self.pause_btn, self.stop_btn, self.open_btn, self.copy_btn, self.back_btn):
            buttons.addWidget(b)
        v.addLayout(buttons)
        self.reset()

    # -- lifecycle ------------------------------------------------------------
    def reset(self) -> None:
        self.out = None
        self.paused = False
        self.finished = False
        self.peak_temp = None
        self.table.reset()
        self.health.reset()
        self.timeline.reset()
        self.log.clear()
        self.bar.setValue(0)
        self._set_status('Starting...', theme.TEXT)
        self.detail.setText('')
        self.activity.setText('Collecting system information...')
        self.times.setText('')
        self.temp.setText('')
        self._running_buttons(True)

    def _running_buttons(self, running: bool) -> None:
        self.pause_btn.setVisible(running)
        self.stop_btn.setVisible(running)
        self.pause_btn.setText('Pause')
        for b in (self.open_btn, self.copy_btn, self.back_btn):
            b.setVisible(not running)

    def _set_status(self, text: str, colour: str) -> None:
        self.status.setText(text)
        self.status.setStyleSheet(f'color: {colour};')

    # -- events from the engine ----------------------------------------------
    def on_event(self, e: dict) -> None:
        kind = e.get('event')
        if kind == 'start':
            self.out = local_path(e.get('out'))
            self.timeline.reset(e.get('duration_s'))
            self._set_status(running_title(e.get('mode') or ''), theme.ACCENT)
            self.detail.setText(f'Reports: {self.out}')
        elif kind == 'section':
            self.activity.setText(e.get('title', ''))
        elif kind == 'pass':
            self.table.on_pass(e)
            self.timeline.add(e['check'], e.get('at_s'), e['status'])
            self.activity.setText(f'{e["check"]}: {e["method"]}, pass {e["round"]}')
            t = e.get('max_temp_c')
            if t is not None:
                self.peak_temp = max(self.peak_temp or t, t)
                self.temp.setText(f'Temp {t:.0f} °C (peak {self.peak_temp:.0f} °C)')
        elif kind == 'unsupported':
            self.table.on_unsupported(e)
        elif kind == 'progress':
            self.bar.setValue(int(e.get('fraction', 0) * 1000))
            eta = e.get('eta_s')
            self.times.setText(f'Elapsed {hms(e.get("elapsed_s"))}'
                               + (f'   Remaining {hms(eta)}' if eta is not None else ''))
        elif kind == 'health':
            self.health.set_cards(e.get('cards') or [])
        elif kind == 'result':
            self.table.on_result(e)
        elif kind == 'log':
            self.log.appendPlainText(e.get('line', ''))
        elif kind == 'paused':
            self._set_status('Paused', theme.WARN)
        elif kind == 'resumed':
            self._set_status('Running', theme.ACCENT)
        elif kind == 'finish':
            self.show_finished(history.verdict(e.get('state'), e.get('counts')), e.get('out'))

    def show_finished(self, verdict: str, out: str | None = None) -> None:
        self.finished = True
        self.out = local_path(out) or self.out
        title, text = VERDICTS[verdict]
        self._set_status(title, theme.VERDICT_COLOURS[verdict])
        self.detail.setText(text + (f'\nReports: {self.out}' if self.out else ''))
        if verdict == 'clean':
            self.bar.setValue(1000)
        self.times.setText(self.times.text().split('   Remaining')[0])
        self.activity.setText('')
        self._running_buttons(False)

    def show_crash(self, code: int, stderr_tail: list, did_not_start: bool = False) -> None:
        self.finished = True
        if did_not_start:
            self._set_status('The test engine did not start', theme.FAIL)
            self.detail.setText('No tests ran and nothing was written. The log below shows what was '
                                'launched; please include it if you report this.')
        else:
            self._set_status('The test engine stopped unexpectedly', theme.FAIL)
            self.detail.setText(f'Exit code {code}. Any partial results are in the report folder.')
        self.log.appendPlainText('\n'.join(stderr_tail))
        self.log_btn.setChecked(True)
        self._running_buttons(False)

    # -- buttons -----------------------------------------------------------------
    def _pause_clicked(self) -> None:
        self.paused = not self.paused
        self.pause_btn.setText('Resume' if self.paused else 'Pause')
        (self.pause_requested if self.paused else self.resume_requested).emit()
        if self.paused:
            self._set_status('Pausing after the current step...', theme.WARN)

    def _toggle_log(self, on: bool) -> None:
        self.log.setVisible(on)
        self.log_btn.setText('Hide log' if on else 'Show log')

    def _open_folder(self) -> None:
        if self.out:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.out))

    def _copy_summary(self) -> None:
        if not self.out:
            return
        try:
            text = (pathlib.Path(self.out) / 'support-summary.txt').read_text(encoding='utf-8')
        except OSError:
            return
        QGuiApplication.clipboard().setText(text)
        self.copy_btn.setText('Copied')
