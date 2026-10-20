"""In-process hashing, test-data generation and page-cache control.

These are the Python-side read paths. They exist alongside the coreutils
paths (dd, sha256sum) so a fault can be cross-checked by two independent
implementations.
"""
from __future__ import annotations

import hashlib
import mmap
import os
import time
from dataclasses import asdict, dataclass, field

MIB = 1024 ** 2
GIB = 1024 ** 3
BLOCK = 4 * MIB      # read/write unit; also the O_DIRECT buffer size
CHUNK = 32 * MIB     # granularity for localising a corrupted region


@dataclass
class HashResult:
    sha256: str = ''
    bytes: int = 0
    elapsed: float = 0.0
    chunk_hashes: list = field(default_factory=list)
    error: str = ''
    unsupported: bool = False

    @property
    def ok(self) -> bool:
        return not self.error

    def to_dict(self, with_chunks: bool = False) -> dict:
        d = asdict(self)
        if not with_chunks:
            d.pop('chunk_hashes')
        return d


class _Hasher:
    """Whole-stream SHA-256 plus one SHA-256 per CHUNK-sized region."""

    def __init__(self, chunk_size: int = CHUNK):
        self.total = hashlib.sha256()
        self.chunk_size = chunk_size
        self.chunk = hashlib.sha256()
        self.chunk_fill = 0
        self.chunks: list = []
        self.count = 0

    def update(self, data) -> None:
        mv = memoryview(data)
        self.total.update(mv)
        self.count += len(mv)
        while len(mv):
            take = min(len(mv), self.chunk_size - self.chunk_fill)
            self.chunk.update(mv[:take])
            self.chunk_fill += take
            mv = mv[take:]
            if self.chunk_fill == self.chunk_size:
                self.chunks.append(self.chunk.hexdigest())
                self.chunk = hashlib.sha256()
                self.chunk_fill = 0

    def result(self, t0: float) -> HashResult:
        chunks = list(self.chunks)
        if self.chunk_fill:
            chunks.append(self.chunk.hexdigest())
        return HashResult(self.total.hexdigest(), self.count, time.monotonic() - t0, chunks)


def diff_chunks(reference: list, observed: list, chunk_size: int = CHUNK) -> list:
    """Return [{'index', 'offset_mib'}] for every chunk that differs."""
    out = []
    for i in range(max(len(reference), len(observed))):
        a = reference[i] if i < len(reference) else None
        b = observed[i] if i < len(observed) else None
        if a != b:
            out.append({'index': i, 'offset_mib': i * chunk_size // MIB})
    return out


def generate_random_file(path, size_mib: int, chunk_size: int = CHUNK) -> HashResult:
    """Write random data and hash it from memory as it is written.

    The reference hash is never read back from disk, so a faulty read path
    cannot contaminate it. (Corruption before hashing in RAM still could.)
    """
    t0 = time.monotonic()
    h = _Hasher(chunk_size)
    remaining = size_mib * MIB
    with open(path, 'wb', buffering=0) as f:
        while remaining:
            data = os.urandom(min(BLOCK, remaining))
            h.update(data)
            mv = memoryview(data)
            while len(mv):
                n = f.write(mv)
                if not n:
                    raise OSError('short write to ' + str(path))
                mv = mv[n:]
            remaining -= len(data)
        os.fsync(f.fileno())
    return h.result(t0)


def evict_cache(path) -> tuple:
    """Ask the kernel to drop this file's clean pages from the page cache.

    Needs no root. Returns (ok, message). Best effort: the kernel may keep
    pages that are mapped or under writeback.
    """
    if not hasattr(os, 'posix_fadvise'):
        return False, 'posix_fadvise not available on this platform'
    try:
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fdatasync(fd)
            os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
        finally:
            os.close(fd)
        return True, 'page cache eviction requested'
    except OSError as e:
        return False, 'eviction failed: ' + repr(e)


def hash_file(path, direct: bool = False, chunk_size: int = CHUNK) -> HashResult:
    """Hash a file in-process, through the page cache or with O_DIRECT."""
    t0 = time.monotonic()
    h = _Hasher(chunk_size)
    if direct:
        if not hasattr(os, 'O_DIRECT') or not hasattr(os, 'preadv'):
            return HashResult(error='O_DIRECT not available on this platform', unsupported=True)
        try:
            fd = os.open(path, os.O_RDONLY | os.O_DIRECT)
        except OSError as e:
            return HashResult(error='O_DIRECT open failed: ' + repr(e), unsupported=True)
        buf = mmap.mmap(-1, BLOCK)  # page-aligned, as O_DIRECT requires
        try:
            offset = 0
            while True:
                n = os.preadv(fd, [buf], offset)
                if n <= 0:
                    break
                h.update(memoryview(buf)[:n])
                offset += n
                if n < BLOCK:
                    break
        except OSError as e:
            return HashResult(bytes=h.count, error='O_DIRECT read failed: ' + repr(e))
        finally:
            os.close(fd)
            buf.close()
        return h.result(t0)
    buf = bytearray(BLOCK)
    try:
        with open(path, 'rb', buffering=0) as f:
            while True:
                n = f.readinto(buf)
                if not n:
                    break
                h.update(memoryview(buf)[:n])
    except OSError as e:
        return HashResult(bytes=h.count, error='read failed: ' + repr(e))
    return h.result(t0)


def hash_device_stream(path: str, total: int) -> HashResult:
    """Read exactly `total` bytes from a character device such as /dev/zero."""
    t0 = time.monotonic()
    h = _Hasher(CHUNK)
    buf = bytearray(BLOCK)
    try:
        with open(path, 'rb', buffering=0) as f:
            while h.count < total:
                want = min(BLOCK, total - h.count)
                n = f.readinto(memoryview(buf)[:want])
                if not n:
                    break
                h.update(memoryview(buf)[:n])
    except OSError as e:
        return HashResult(bytes=h.count, error='read failed: ' + repr(e))
    return h.result(t0)
