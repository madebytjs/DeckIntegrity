"""Run external commands and two-stage pipelines safely.

Every command gets a deadline. On timeout or interruption the whole process
group is killed, so no orphaned dd/sha256sum keeps running after we give up.
Stderr goes to temporary files rather than pipes so a chatty producer can
never deadlock the pipeline.
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass, field

MAX_OUTPUT = 64000
_SESSION = {'start_new_session': True} if os.name == 'posix' else {}


@dataclass
class CommandResult:
    argv: list
    returncode: int | None = None
    stdout: str = ''
    stderr: str = ''
    elapsed: float = 0.0
    timed_out: bool = False
    error: str = ''

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out and not self.error

    @property
    def missing(self) -> bool:
        return self.error.startswith('not installed')

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PipelineResult:
    producer: CommandResult
    consumer: CommandResult
    elapsed: float = 0.0
    error: str = ''

    @property
    def ok(self) -> bool:
        return self.producer.ok and self.consumer.ok and not self.error

    @property
    def missing(self) -> bool:
        return self.producer.missing or self.consumer.missing

    @property
    def timed_out(self) -> bool:
        return self.producer.timed_out or self.consumer.timed_out

    def to_dict(self) -> dict:
        return {'producer': self.producer.to_dict(), 'consumer': self.consumer.to_dict(),
                'elapsed': self.elapsed, 'error': self.error}


def _kill(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        if os.name == 'posix':
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
    except (ProcessLookupError, PermissionError, OSError):
        pass
    try:
        proc.wait(timeout=5)
    except Exception:
        pass


def _read_tmp(f) -> str:
    f.seek(0)
    return f.read()[:MAX_OUTPUT].decode('utf-8', errors='replace')


def _missing(argv: list) -> str:
    return '' if shutil.which(argv[0]) else 'not installed: ' + argv[0]


def run_command(argv: list, timeout: float) -> CommandResult:
    res = CommandResult(argv=list(argv))
    res.error = _missing(argv)
    if res.error:
        return res
    t0 = time.monotonic()
    proc = None
    with tempfile.TemporaryFile() as err:
        try:
            proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=err, **_SESSION)
            try:
                out, _ = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                _kill(proc)
                out, _ = proc.communicate()
                res.timed_out = True
            res.returncode = proc.returncode
            res.stdout = out[:MAX_OUTPUT].decode('utf-8', errors='replace')
        except BaseException as e:
            _kill(proc)
            if not isinstance(e, Exception):
                raise
            res.error = repr(e)
        finally:
            res.elapsed = time.monotonic() - t0
            res.stderr = _read_tmp(err)
    return res


def run_pipeline(producer: list, consumer: list, timeout: float) -> PipelineResult:
    """Equivalent of `producer | consumer` without a shell."""
    res = PipelineResult(CommandResult(argv=list(producer)), CommandResult(argv=list(consumer)))
    res.producer.error = _missing(producer)
    res.consumer.error = _missing(consumer)
    if res.missing:
        return res
    t0 = time.monotonic()
    deadline = t0 + timeout
    p1 = p2 = None
    with tempfile.TemporaryFile() as e1, tempfile.TemporaryFile() as e2:
        try:
            p1 = subprocess.Popen(producer, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                  stderr=e1, **_SESSION)
            p2 = subprocess.Popen(consumer, stdin=p1.stdout, stdout=subprocess.PIPE,
                                  stderr=e2, **_SESSION)
            p1.stdout.close()  # consumer owns the read end; producer gets SIGPIPE if it dies
            out = None
            try:
                out, _ = p2.communicate(timeout=max(0.1, deadline - time.monotonic()))
                p1.wait(timeout=max(0.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                res.producer.timed_out = p1.poll() is None
                res.consumer.timed_out = p2.poll() is None
                _kill(p1)
                _kill(p2)
                if out is None:
                    out, _ = p2.communicate()
            res.consumer.stdout = (out or b'')[:MAX_OUTPUT].decode('utf-8', errors='replace')
            res.producer.returncode = p1.returncode
            res.consumer.returncode = p2.returncode
        except BaseException as e:
            _kill(p1)
            _kill(p2)
            if not isinstance(e, Exception):
                raise
            res.error = repr(e)
        finally:
            res.elapsed = time.monotonic() - t0
            res.producer.elapsed = res.consumer.elapsed = res.elapsed
            res.producer.stderr = _read_tmp(e1)
            res.consumer.stderr = _read_tmp(e2)
    return res
