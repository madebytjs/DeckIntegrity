"""Custom widgets: live results table and the pass timeline."""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QMessageBox,
                               QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from deck_integrity.gui import theme

COLUMNS = ['Check', 'Method', 'Passes', 'Faults', 'Errors', 'Status']
# Engine check names are technical and appear in reports; the app shows plain names.
FRIENDLY = {'2 GiB /dev/zero stream': 'Processor and memory', 'RAM-backed file': 'Memory (RAM)',
            'SSD test file': 'Storage (SSD)'}


def friendly(check: str) -> str:
    return FRIENDLY.get(check, check)


class ResultsTable(QTableWidget):
    """One row per check/method, updated live from pass and result events."""

    def __init__(self, parent=None):
        super().__init__(0, len(COLUMNS), parent)
        self.setHorizontalHeaderLabels(COLUMNS)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(27)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setSelectionMode(QAbstractItemView.NoSelection)
        self.setFocusPolicy(Qt.NoFocus)
        self.setShowGrid(False)
        h = self.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.Stretch)
        for c in range(2, len(COLUMNS)):
            h.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        self.rows: dict = {}

    def reset(self) -> None:
        self.setRowCount(0)
        self.rows = {}

    def _row(self, check: str, method: str) -> dict:
        key = (check, method)
        if key not in self.rows:
            r = self.rowCount()
            self.insertRow(r)
            self.rows[key] = {'row': r, 'passes': 0, 'faults': 0, 'errors': 0, 'status': 'RUNNING'}
            first_of_check = not any(k[0] == check for k in self.rows if k != key)
            self._set(r, 0, friendly(check) if first_of_check else '', theme.MUTED)
            self._set(r, 1, method)
            self._paint(key)
        return self.rows[key]

    def _set(self, r, c, text, colour=None, bold=False):
        item = QTableWidgetItem(str(text))
        if colour:
            item.setForeground(QColor(colour))
        if bold:
            f = QFont()
            f.setBold(True)
            item.setFont(f)
        if c >= 2:
            item.setTextAlignment(Qt.AlignCenter)
        self.setItem(r, c, item)

    def _paint(self, key) -> None:
        d = self.rows[key]
        r = d['row']
        self._set(r, 2, d['passes'])
        self._set(r, 3, d['faults'], theme.FAIL if d['faults'] else None, bold=bool(d['faults']))
        self._set(r, 4, d['errors'], theme.WARN if d['errors'] else None)
        label = {'RUNNING': 'running'}.get(d['status'], d['status'])
        self._set(r, 5, label, theme.STATUS_COLOURS.get(d['status']), bold=True)

    def on_pass(self, e: dict) -> None:
        d = self._row(e['check'], e['method'])
        d['passes'] += 1
        if e['status'] == 'mismatch':
            d['faults'] += 1
        elif e['status'] == 'error':
            d['errors'] += 1
        self._paint((e['check'], e['method']))

    def on_unsupported(self, e: dict) -> None:
        d = self._row(e['check'], e['method'])
        d['status'] = 'SKIP'
        self._paint((e['check'], e['method']))

    def on_result(self, e: dict) -> None:
        """Final verdict for a method; results named '<check>: <method>'."""
        check, sep, method = e['test'].partition(': ')
        if not sep or (check, method) not in self.rows:
            return
        self.rows[(check, method)]['status'] = e['status']
        self._paint((check, method))


HEALTH_COLOURS = {'good': theme.OK, 'warn': theme.WARN, 'bad': theme.FAIL,
                  'pending': theme.ACCENT, 'unknown': theme.SKIP}


class HealthCards(QWidget):
    """Row of tappable cards, one per health area; tap for the full detail."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.row = QHBoxLayout(self)
        self.row.setContentsMargins(0, 0, 0, 0)
        self.row.setSpacing(8)
        self.cards: dict = {}

    def reset(self) -> None:
        while self.row.count():
            w = self.row.takeAt(0).widget()
            if w:
                w.deleteLater()
        self.cards = {}
        self.setVisible(False)

    def set_cards(self, cards: list) -> None:
        self.reset()
        for c in cards:
            colour = HEALTH_COLOURS.get(c['status'], theme.SKIP)
            b = QPushButton()
            b.setObjectName('healthcard')
            b.setMinimumHeight(64)
            b.setStyleSheet(f'QPushButton#healthcard {{ border-left: 5px solid {colour}; '
                            f'text-align: left; padding: 6px 10px; }}')
            v = QVBoxLayout(b)
            v.setContentsMargins(12, 6, 8, 6)
            v.setSpacing(2)
            title = QLabel(c['title'])
            title.setStyleSheet(f'color: {theme.MUTED}; font-size: 12px;')
            value = QLabel(c['value'])
            value.setStyleSheet(f'color: {colour}; font-weight: 600; font-size: 14px;')
            value.setWordWrap(True)
            for lbl in (title, value):
                lbl.setAttribute(Qt.WA_TransparentForMouseEvents)
                v.addWidget(lbl)
            b.setToolTip(c['detail'])
            b.clicked.connect(lambda _=False, c=c: QMessageBox.information(self, c['title'],
                                                                         f'{c["value"]}\n\n{c["detail"]}'))
            self.row.addWidget(b, 1)
            self.cards[c['key']] = {'card': c, 'button': b}
        self.setVisible(bool(cards))


class Timeline(QWidget):
    """Every pass as a tick on a time axis, one lane per check. Red ticks are
    faults, amber errors. Shows at a glance whether faults cluster in time."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(76)
        self.setMaximumHeight(96)
        self.reset()

    def reset(self, duration_s: float | None = None) -> None:
        self.duration_s = duration_s
        self.lanes: list = []
        self.ticks: list = []   # (lane, at_s, status)
        self.update()

    def add(self, check: str, at_s: float, status: str) -> None:
        if check not in self.lanes:
            self.lanes.append(check)
        self.ticks.append((self.lanes.index(check), at_s or 0.0, status))
        self.update()

    def span(self) -> float:
        latest = max((t[1] for t in self.ticks), default=0.0)
        return max(self.duration_s or 0.0, latest, 1.0)

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), QColor(theme.PANEL))
        n = max(1, len(self.lanes))
        label_w, axis_h = 150, 18
        lane_h = (h - axis_h - 8) / n
        span = self.span()
        f = p.font()
        f.setPixelSize(12)
        p.setFont(f)
        for i, name in enumerate(self.lanes):
            y = 4 + i * lane_h
            p.setPen(QColor(theme.MUTED))
            p.drawText(QRectF(6, y, label_w - 10, lane_h), Qt.AlignVCenter | Qt.AlignLeft,
                       friendly(name))
            p.fillRect(QRectF(label_w, y + lane_h / 2 - 1, w - label_w - 8, 2), QColor(theme.BORDER))
        # Draw ok ticks first so faults stay visible on top.
        for wanted in ('ok', 'error', 'mismatch'):
            for lane, at, status in self.ticks:
                if status != wanted:
                    continue
                x = label_w + (w - label_w - 8) * min(1.0, at / span)
                y = 4 + lane * lane_h
                tall = lane_h * (0.8 if status != 'ok' else 0.5)
                p.fillRect(QRectF(x - 1, y + (lane_h - tall) / 2, 3 if status != 'ok' else 2, tall),
                           QColor(theme.STATUS_COLOURS[status]))
        p.setPen(QColor(theme.MUTED))
        for frac in (0, 0.25, 0.5, 0.75, 1.0):
            x = label_w + (w - label_w - 8) * frac
            secs = int(span * frac)
            text = f'{secs // 3600}:{secs % 3600 // 60:02d}:{secs % 60:02d}' if span >= 3600 \
                else f'{secs // 60}:{secs % 60:02d}'
            # Keep the end labels inside the widget.
            box = QRectF(min(max(x - 40, label_w - 40), w - 84), h - axis_h, 80, axis_h)
            p.drawText(box, Qt.AlignCenter, text)
        p.end()
