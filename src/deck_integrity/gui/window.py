"""Main window: header, setup and run screens, and safe closing mid-run."""
from __future__ import annotations

import pathlib
import platform

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QMainWindow, QMessageBox, QStackedWidget,
                               QVBoxLayout, QWidget)

from deck_integrity import VERSION, cli, sysinfo
from deck_integrity.gui.engine import STOP_GRACE_MS, Engine
from deck_integrity.gui.run_page import RunPage
from deck_integrity.gui.setup_page import SetupPage, label


def device_line() -> str:
    rel = sysinfo.os_release()
    os_name = f'{rel.get("NAME", platform.system())} {rel.get("VERSION_ID", "")}'.strip()
    return f'{cli.device_label(sysinfo.dmi()).replace("-", " ")}  |  {os_name}  |  kernel {platform.release()}'


class MainWindow(QMainWindow):
    def __init__(self, engine: Engine | None = None):
        super().__init__()
        self.setWindowTitle('Deck Integrity Suite')
        self.engine = engine or Engine(self)
        self._closing = False

        root = QWidget()
        root.setObjectName('root')
        v = QVBoxLayout(root)
        v.setContentsMargins(20, 16, 20, 16)
        v.setSpacing(14)
        head = QHBoxLayout()
        icon = pathlib.Path(__file__).with_name('icon.png')
        if icon.exists():
            logo = QLabel()
            logo.setPixmap(QPixmap(str(icon)).scaled(40, 40, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            head.addWidget(logo)
        head.addWidget(label('Deck Integrity Suite', 'title'))
        head.addStretch()
        head.addWidget(label(f'v{VERSION}', 'muted'))
        v.addLayout(head)
        self.device = label(device_line(), 'subtitle')
        v.addWidget(self.device)

        self.stack = QStackedWidget()
        self.setup = SetupPage()
        self.run_page = RunPage()
        self.stack.addWidget(self.setup)
        self.stack.addWidget(self.run_page)
        v.addWidget(self.stack, 1)
        self.setCentralWidget(root)

        self.setup.start_requested.connect(self.start)
        self.run_page.pause_requested.connect(self.engine.pause)
        self.run_page.resume_requested.connect(self.engine.resume)
        self.run_page.stop_requested.connect(self.confirm_stop)
        self.run_page.back_requested.connect(self.show_setup)
        self.engine.event.connect(self.run_page.on_event)
        self.engine.stderr_line.connect(self.run_page.log.appendPlainText)
        self.engine.ended.connect(self.on_engine_ended)
        self.show_setup()

    def show_setup(self) -> None:
        self.setup.refresh_history()
        self.stack.setCurrentWidget(self.setup)

    def start(self, options: dict) -> None:
        self.run_page.reset()
        self.stack.setCurrentWidget(self.run_page)
        self.engine.start(options)

    def confirm_stop(self) -> bool:
        if not self.engine.running:
            return True
        answer = QMessageBox.question(
            self, 'Stop the test?',
            'Stop now? Everything tested so far is saved to the report.',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return False
        self.run_page.status.setText('Stopping after the current step...')
        self.engine.stop()
        QTimer.singleShot(STOP_GRACE_MS, self.engine.kill)   # last resort if it hangs
        return True

    def on_engine_ended(self, code: int, saw_finish: bool) -> None:
        if not saw_finish:
            self.run_page.show_crash(code, self.engine.stderr_tail)
        if self._closing:
            self.close()

    def closeEvent(self, ev) -> None:
        if self.engine.running and not self._closing:
            if not self.confirm_stop():
                ev.ignore()
                return
            self._closing = True
            ev.ignore()   # close once the engine has written its reports
            return
        ev.accept()
