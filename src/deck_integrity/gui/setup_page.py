"""Setup screen: choose a mode and checks, see past runs."""
from __future__ import annotations

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout,
                               QLabel, QListWidget, QListWidgetItem, QPushButton, QSizePolicy,
                               QSpinBox, QVBoxLayout, QWidget)

from deck_integrity import history, sensors
from deck_integrity.gui import theme

MODES = {
    'quick': ('Quick', 'A fast health check. About 30 seconds.',
              {'passes': 3, 'file_mib': 512, 'ram_mib': 128}),
    'full': ('Full', 'A thorough health check. About 2 minutes.',
             {'passes': 5, 'file_mib': 2048, 'ram_mib': 256}),
    'endurance': ('Endurance', 'Repeats the checks for a set time, to catch problems that only '
                               'show up sometimes.',
                  {'passes': 5, 'file_mib': 2048, 'ram_mib': 256}),
}
CHECKS = [('zero', 'Processor and memory: streams 2 GiB of data through the CPU and RAM'),
          ('ram', 'Memory (RAM): stores test data in memory and reads it back'),
          ('ssd', 'Storage (SSD): writes one test file, then reads it back five ways')]
INTRO = ('A hardware health check for your data. It tests whether your storage, memory and '
         'processor store and handle data correctly, by checking the same data many times over. '
         'It does not measure SSD lifespan, battery health or graphics performance.')
DURATIONS = [15, 30, 60, 120, 240, 480]
FILE_SIZES = [256, 512, 1024, 2048, 4096]
RAM_SIZES = [64, 128, 256, 512]
VERDICT_TEXT = {'clean': 'no faults', 'faults': 'FAULTS', 'errors': 'errors', 'stopped': 'stopped',
                'unknown': 'unknown'}


def panel() -> QFrame:
    f = QFrame()
    f.setObjectName('panel')
    return f


def label(text: str, name: str | None = None, wrap: bool = False) -> QLabel:
    lbl = QLabel(text)
    if name:
        lbl.setObjectName(name)
    lbl.setWordWrap(wrap)
    return lbl


class SetupPage(QWidget):
    start_requested = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        outer = QHBoxLayout(self)
        outer.setSpacing(16)
        outer.addWidget(self._build_options(), 3)
        outer.addWidget(self._build_history(), 2)
        self.set_mode('quick')

    # -- left: options ----------------------------------------------------
    def _build_options(self) -> QFrame:
        box = panel()
        v = QVBoxLayout(box)
        v.setContentsMargins(18, 18, 18, 18)
        v.setSpacing(12)
        v.addWidget(label(INTRO, 'muted', wrap=True))
        v.addWidget(label('Choose a test', 'h2'))
        self.mode_group = QButtonGroup(self)
        self.mode_buttons = {}
        row = QHBoxLayout()
        for key, (title, desc, _) in MODES.items():
            # QPushButton cannot wrap text, so the card carries its own word-wrapped labels.
            b = QPushButton()
            b.setObjectName('mode')
            b.setCheckable(True)
            b.setMinimumHeight(96)
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            inner = QVBoxLayout(b)
            inner.setContentsMargins(14, 10, 14, 10)
            name = label(title, 'h2')
            text = label(desc, 'muted', wrap=True)
            for lbl in (name, text):
                lbl.setAttribute(Qt.WA_TransparentForMouseEvents)
                inner.addWidget(lbl)
            inner.addStretch()
            b.clicked.connect(lambda _=False, k=key: self.set_mode(k))
            self.mode_group.addButton(b)
            self.mode_buttons[key] = b
            row.addWidget(b, 1)
        v.addLayout(row)

        dur = QHBoxLayout()
        dur.addWidget(label('Endurance length'))
        self.duration = QComboBox()
        for m in DURATIONS:
            self.duration.addItem(f'{m // 60} h' if m >= 60 and m % 60 == 0 else f'{m} min', m)
        self.duration.setCurrentIndex(DURATIONS.index(60))
        dur.addWidget(self.duration)
        dur.addStretch()
        v.addLayout(dur)

        v.addWidget(label('Checks', 'h2'))
        self.check_boxes = {}
        for key, text in CHECKS:
            cb = QCheckBox(text)
            cb.setChecked(True)
            cb.toggled.connect(self._refresh)
            self.check_boxes[key] = cb
            v.addWidget(cb)

        self.ssd_health = QCheckBox("SSD health report: reads the drive's own wear and error log "
                                    '(asks for your admin password)')
        v.addWidget(self.ssd_health)
        self.adv_toggle = QPushButton('Advanced settings')
        self.adv_toggle.setCheckable(True)
        self.adv_toggle.toggled.connect(lambda on: self.advanced.setVisible(on))
        v.addWidget(self.adv_toggle, 0, Qt.AlignLeft)
        self.advanced = QWidget()
        g = QGridLayout(self.advanced)
        g.setContentsMargins(0, 0, 0, 0)
        self.passes = QSpinBox()
        self.passes.setRange(1, 100)
        self.file_mib = QComboBox()
        for s in FILE_SIZES:
            self.file_mib.addItem(f'{s} MiB' if s < 1024 else f'{s // 1024} GiB', s)
        self.ram_mib = QComboBox()
        for s in RAM_SIZES:
            self.ram_mib.addItem(f'{s} MiB', s)
        self.keep_file = QCheckBox('Keep the SSD test file afterwards')
        g.addWidget(label('Passes per method'), 0, 0)
        g.addWidget(self.passes, 0, 1)
        g.addWidget(label('SSD test file'), 1, 0)
        g.addWidget(self.file_mib, 1, 1)
        g.addWidget(label('RAM test file'), 2, 0)
        g.addWidget(self.ram_mib, 2, 1)
        g.addWidget(self.keep_file, 3, 0, 1, 2)
        self.advanced.setVisible(False)
        v.addWidget(self.advanced)

        v.addStretch()
        self.notice = label('', 'muted', wrap=True)
        v.addWidget(self.notice)
        self.start_btn = QPushButton('Start test')
        self.start_btn.setObjectName('primary')
        self.start_btn.setMinimumHeight(56)
        self.start_btn.clicked.connect(lambda: self.start_requested.emit(self.options()))
        v.addWidget(self.start_btn)
        for w in (self.passes, self.file_mib, self.ram_mib):
            (w.valueChanged if isinstance(w, QSpinBox) else w.currentIndexChanged).connect(self._refresh)
        self.duration.currentIndexChanged.connect(self._refresh)
        return box

    # -- right: past runs ---------------------------------------------------
    def _build_history(self) -> QFrame:
        box = panel()
        v = QVBoxLayout(box)
        v.setContentsMargins(18, 18, 18, 18)
        v.addWidget(label('Past runs', 'h2'))
        v.addWidget(label('Folder names show date, device, OS version and mode, '
                          'so runs from different devices line up.', 'muted', wrap=True))
        self.runs = QListWidget()
        self.runs.setWordWrap(True)
        self.runs.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.runs.itemActivated.connect(self._open_run)
        v.addWidget(self.runs, 1)
        open_btn = QPushButton('Open selected report folder')
        open_btn.clicked.connect(lambda: self._open_run(self.runs.currentItem()))
        v.addWidget(open_btn)
        return box

    def refresh_history(self) -> None:
        self.runs.clear()
        runs = history.load_runs()
        if not runs:
            self.runs.addItem('No runs yet. Reports appear here after your first test.')
            return
        for r in runs:
            item = QListWidgetItem(f'{r["name"]}\n{VERDICT_TEXT[r["verdict"]]}'
                                   + (f' - first at pass {r["first_failure"]["pass"]}'
                                      if r.get('first_failure') else ''))
            item.setForeground(QColor(theme.VERDICT_COLOURS[r['verdict']]))
            item.setData(Qt.UserRole, r['folder'])
            self.runs.addItem(item)

    def _open_run(self, item) -> None:
        folder = item.data(Qt.UserRole) if item else None
        if folder:
            QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    # -- state ---------------------------------------------------------------
    def set_mode(self, key: str) -> None:
        self.mode = key
        self.mode_buttons[key].setChecked(True)
        preset = MODES[key][2]
        self.passes.setValue(preset['passes'])
        self.file_mib.setCurrentIndex(FILE_SIZES.index(preset['file_mib']))
        self.ram_mib.setCurrentIndex(RAM_SIZES.index(preset['ram_mib']))
        self._refresh()

    def options(self) -> dict:
        return {'mode': self.mode,
                'endurance_min': self.duration.currentData() if self.mode == 'endurance' else None,
                'passes': self.passes.value(), 'file_mib': self.file_mib.currentData(),
                'ram_mib': self.ram_mib.currentData(),
                'skip': [k for k, cb in self.check_boxes.items() if not cb.isChecked()],
                'keep_file': self.keep_file.isChecked(), 'ssd_health': self.ssd_health.isChecked()}

    def _refresh(self) -> None:
        endurance = self.mode == 'endurance'
        self.duration.setEnabled(endurance)
        self.passes.setEnabled(not endurance)
        any_check = any(cb.isChecked() for cb in self.check_boxes.values())
        self.start_btn.setEnabled(any_check)
        parts = []
        if self.check_boxes['ssd'].isChecked():
            size = self.file_mib.currentText()
            parts.append(f'Writes a temporary {size} file to your home folder once and deletes it '
                         'afterwards.')
        bat = sensors.battery()
        if bat and (bat.get('status') or '').lower() == 'discharging':
            low = bat.get('capacity_pct') is not None and bat['capacity_pct'] < 30
            if endurance or low:
                parts.append(f'Battery {bat.get("capacity_pct")}% and not charging: plug in power '
                             'so the device does not shut down mid-test.')
        if not any_check:
            parts.append('Select at least one check.')
        self.notice.setText(' '.join(parts))
