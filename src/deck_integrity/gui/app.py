"""GUI entry point: python -m deck_integrity.gui"""
from __future__ import annotations

import pathlib
import sys

from deck_integrity import VERSION


def selftest(app, win) -> int:
    """Packaging check used by CI: the window builds AND the GUI can start the real
    engine and receive its events (the step that broke in the first AppImage)."""
    import tempfile

    from PySide6.QtCore import QEventLoop, QTimer
    win.resize(1280, 800)
    win.show()
    app.processEvents()
    if win.grab().isNull():
        print('selftest FAILED: window did not render')
        return 1
    events = []
    win.engine.event.connect(lambda e: events.append(e.get('event')))
    win.engine.stderr_line.connect(lambda line: print('engine:', line))
    loop = QEventLoop()
    win.engine.ended.connect(lambda *a: loop.quit())
    QTimer.singleShot(90000, loop.quit)
    with tempfile.TemporaryDirectory() as out:
        win.accept_disclaimer = lambda: True
        win.start({'mode': 'quick', 'passes': 1, 'file_mib': 32, 'ram_mib': 32,
                   'skip': ['sysinfo', 'zero', 'ram'], 'output': out})
        loop.exec()
        if win.engine.running:
            win.engine.kill()
    ok = 'start' in events and 'finish' in events and 'pass' in events
    print('selftest', 'ok' if ok else 'FAILED', 'platform', app.platformName(),
          'engine events:', sorted(set(events)))
    return 0 if ok else 1


def main(argv=None) -> int:
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        print('The GUI needs PySide6. The command-line tool works without it:\n'
              '  python3 deck-integrity.pyz --help', file=sys.stderr)
        return 2
    from deck_integrity.gui.theme import QSS
    from deck_integrity.gui.window import MainWindow

    app = QApplication(sys.argv if argv is None else argv)
    app.setApplicationName('DeckIntegrity')
    app.setApplicationVersion(VERSION)
    app.setDesktopFileName('deck-integrity')   # matches the .desktop file, for the Wayland icon
    app.setStyleSheet(QSS)
    icon = pathlib.Path(__file__).with_name('icon.png')
    if icon.exists():
        from PySide6.QtGui import QIcon
        app.setWindowIcon(QIcon(str(icon)))
    win = MainWindow()
    if '--selftest' in app.arguments():
        return selftest(app, win)
    screen = app.primaryScreen().availableGeometry()
    if screen.width() <= 1400:
        win.showMaximized()   # Steam Deck / Legion Go S style screens
    else:
        win.resize(1280, 800)
        win.show()
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
