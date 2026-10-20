"""Dark theme sized for handheld screens (1280x800 and up), touch-friendly."""

BG = '#121519'
PANEL = '#1b2027'
PANEL_HI = '#252c35'
BORDER = '#323b47'
TEXT = '#e6edf3'
MUTED = '#8b98a8'
ACCENT = '#b26bff'          # violet from the logo
ACCENT_FROM = '#ff4f86'     # logo gradient: pink ...
ACCENT_TO = '#7b3cff'       # ... to violet
OK = '#3fb950'
FAIL = '#f85149'
WARN = '#d29922'
SKIP = '#6e7781'

STATUS_COLOURS = {'PASS': OK, 'FAIL': FAIL, 'WARN': WARN, 'ERROR': WARN, 'SKIP': SKIP,
                  'INFO': MUTED, 'RUNNING': ACCENT, 'ok': OK, 'mismatch': FAIL, 'error': WARN}
VERDICT_COLOURS = {'clean': OK, 'faults': FAIL, 'errors': WARN, 'stopped': WARN, 'unknown': SKIP}

QSS = f"""
* {{ font-size: 15px; color: {TEXT}; }}
QMainWindow, QWidget#root {{ background: {BG}; }}
QLabel#title {{ font-size: 22px; font-weight: 600; }}
QLabel#subtitle, QLabel#muted {{ color: {MUTED}; }}
QLabel#h2 {{ font-size: 17px; font-weight: 600; }}
QLabel#big {{ font-size: 28px; font-weight: 600; }}
QFrame#panel {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 10px; }}
QPushButton {{
    background: {PANEL_HI}; border: 1px solid {BORDER}; border-radius: 8px;
    padding: 10px 18px; min-height: 28px;
}}
QPushButton:hover {{ border-color: {ACCENT}; }}
QPushButton:disabled {{ color: {MUTED}; border-color: {PANEL_HI}; }}
QPushButton#primary {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {ACCENT_FROM}, stop:1 {ACCENT_TO});
    color: white; font-weight: 600; border: none;
}}
QPushButton#primary:disabled {{ background: {PANEL_HI}; color: {MUTED}; }}
QPushButton#danger {{ background: transparent; color: {FAIL}; border: 1px solid {FAIL}; }}
QPushButton#mode {{ text-align: left; padding: 14px 16px; min-height: 64px; }}
QPushButton#mode:checked {{ border: 2px solid {ACCENT}; background: #2a1f3d; }}
QCheckBox {{ spacing: 10px; min-height: 32px; }}
QCheckBox::indicator {{ width: 22px; height: 22px; }}
QComboBox, QSpinBox {{
    background: {PANEL_HI}; border: 1px solid {BORDER}; border-radius: 6px;
    padding: 6px 10px; min-height: 28px;
}}
QProgressBar {{
    background: {PANEL_HI}; border: none; border-radius: 6px; height: 14px; text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {ACCENT_FROM}, stop:1 {ACCENT_TO});
    border-radius: 6px;
}}
QTableWidget {{
    background: {PANEL}; border: none; gridline-color: {BORDER};
    selection-background-color: {PANEL_HI};
}}
QHeaderView::section {{
    background: {PANEL}; color: {MUTED}; border: none; border-bottom: 1px solid {BORDER};
    padding: 6px; font-weight: 600;
}}
QListWidget {{ background: {PANEL}; border: none; }}
QListWidget::item {{ padding: 8px; border-bottom: 1px solid {BORDER}; }}
QPlainTextEdit {{
    background: #0b0d10; border: 1px solid {BORDER}; border-radius: 6px;
    font-family: monospace; font-size: 12px;
}}
QScrollBar:vertical {{ width: 14px; background: {PANEL}; }}
QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 6px; min-height: 40px; }}
"""
