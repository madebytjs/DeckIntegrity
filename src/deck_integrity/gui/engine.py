"""Runs the command-line engine as a child process and relays its events.

The GUI never runs tests in-process: the engine is the same tested CLI, started
with --events. That keeps a GUI crash from corrupting a run (and vice versa),
and stopping uses the engine's own clean-shutdown path.
"""
from __future__ import annotations

import json
import os
import pathlib
import sys

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, Signal

import deck_integrity

STOP_GRACE_MS = 15000


def engine_command(options: dict) -> list:
    """Build the engine argument list from GUI options."""
    args = ['-m', 'deck_integrity', '--events', '--yes']
    if options.get('endurance_min'):
        args += ['--endurance', str(int(options['endurance_min']))]
    else:
        args += ['--passes', str(int(options['passes']))]
    args += ['--file-mib', str(int(options['file_mib'])), '--ram-mib', str(int(options['ram_mib']))]
    for name in options.get('skip', []):
        args += ['--skip', name]
    if options.get('keep_file'):
        args.append('--keep-file')
    if options.get('output'):
        args += ['--output', str(options['output'])]
    return args


def parse_lines(buffer: bytes) -> tuple:
    """Split complete JSON lines off the front of buffer. Returns (events, rest, bad_lines)."""
    *lines, rest = buffer.split(b'\n')
    events, bad = [], []
    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            events.append(json.loads(raw.decode('utf-8', errors='replace')))
        except ValueError:
            bad.append(raw.decode('utf-8', errors='replace'))
    return events, rest, bad


class Engine(QObject):
    event = Signal(dict)
    stderr_line = Signal(str)
    ended = Signal(int, bool)   # exit code, whether a 'finish' event was seen

    def __init__(self, parent=None):
        super().__init__(parent)
        self.proc: QProcess | None = None
        self._buf = b''
        self._err = b''
        self.saw_finish = False
        self.stderr_tail: list = []

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.state() != QProcess.NotRunning

    def start(self, options: dict) -> None:
        if self.running:
            raise RuntimeError('engine already running')
        self._buf, self._err, self.saw_finish, self.stderr_tail = b'', b'', False, []
        p = QProcess(self)
        env = QProcessEnvironment.systemEnvironment()
        # Make the package importable when running from a source checkout.
        root = str(pathlib.Path(deck_integrity.__file__).resolve().parent.parent)
        env.insert('PYTHONPATH', os.pathsep.join(x for x in [root, env.value('PYTHONPATH')] if x))
        env.insert('PYTHONUNBUFFERED', '1')
        p.setProcessEnvironment(env)
        p.readyReadStandardOutput.connect(self._on_stdout)
        p.readyReadStandardError.connect(self._on_stderr)
        p.finished.connect(self._on_finished)
        self.proc = p
        p.start(sys.executable, engine_command(options))

    def send(self, command: str) -> None:
        if self.running:
            self.proc.write((command + '\n').encode())

    def pause(self):
        self.send('pause')

    def resume(self):
        self.send('resume')

    def stop(self) -> None:
        """Ask for a clean stop; the engine finishes the current operation and writes reports."""
        self.send('stop')

    def kill(self) -> None:
        if self.running:
            self.proc.kill()

    def _on_stdout(self) -> None:
        self._buf += bytes(self.proc.readAllStandardOutput())
        events, self._buf, bad = parse_lines(self._buf)
        for line in bad:
            self.stderr_line.emit('unparsed engine output: ' + line)
        for e in events:
            if e.get('event') == 'finish':
                self.saw_finish = True
            self.event.emit(e)

    def _on_stderr(self) -> None:
        self._err += bytes(self.proc.readAllStandardError())
        *lines, self._err = self._err.split(b'\n')
        for raw in lines:
            line = raw.decode('utf-8', errors='replace').rstrip()
            if line:
                self.stderr_tail = (self.stderr_tail + [line])[-40:]
                self.stderr_line.emit(line)

    def _on_finished(self, code: int, _status) -> None:
        self._on_stdout()
        self.ended.emit(code, self.saw_finish)
