import hashlib
import os
import pathlib
import tempfile
import unittest

from deck_integrity import hashing
from deck_integrity.hashing import MIB


class HashingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())

    def tearDown(self):
        for p in self.tmp.glob('*'):
            p.unlink()
        self.tmp.rmdir()

    def test_reference_matches_independent_read(self):
        path = self.tmp / 'f.bin'
        ref = hashing.generate_random_file(path, 9)
        self.assertEqual(ref.bytes, 9 * MIB)
        self.assertEqual(ref.sha256, hashlib.sha256(path.read_bytes()).hexdigest())

    def test_chunk_hashes_cover_file_including_partial_tail(self):
        path = self.tmp / 'f.bin'
        ref = hashing.generate_random_file(path, 9, chunk_size=4 * MIB)
        self.assertEqual(len(ref.chunk_hashes), 3)   # 4 + 4 + 1 MiB
        data = path.read_bytes()
        self.assertEqual(ref.chunk_hashes[2], hashlib.sha256(data[8 * MIB:]).hexdigest())

    def test_hash_file_matches_reference(self):
        path = self.tmp / 'f.bin'
        ref = hashing.generate_random_file(path, 5)
        res = hashing.hash_file(path)
        self.assertTrue(res.ok)
        self.assertEqual((res.sha256, res.bytes, res.chunk_hashes), (ref.sha256, ref.bytes, ref.chunk_hashes))

    def test_corruption_is_localised_to_chunk(self):
        path = self.tmp / 'f.bin'
        ref = hashing.generate_random_file(path, 8, chunk_size=2 * MIB)
        data = bytearray(path.read_bytes())
        data[5 * MIB + 17] ^= 0x01   # single bit flip in the third 2 MiB chunk
        path.write_bytes(bytes(data))
        res = hashing.hash_file(path, chunk_size=2 * MIB)
        self.assertNotEqual(res.sha256, ref.sha256)
        diffs = hashing.diff_chunks(ref.chunk_hashes, res.chunk_hashes, chunk_size=2 * MIB)
        self.assertEqual(diffs, [{'index': 2, 'offset_mib': 4}])

    def test_diff_chunks_reports_length_mismatch(self):
        self.assertEqual(len(hashing.diff_chunks(['a', 'b'], ['a'])), 1)

    def test_direct_io_is_supported_or_cleanly_reported(self):
        path = self.tmp / 'f.bin'
        ref = hashing.generate_random_file(path, 4)
        res = hashing.hash_file(path, direct=True)
        if res.unsupported:
            self.assertTrue(res.error)
        else:
            self.assertEqual(res.sha256, ref.sha256)

    def test_device_stream_reads_exact_length(self):
        path = self.tmp / 'zeros'
        path.write_bytes(bytes(3 * MIB))
        res = hashing.hash_device_stream(str(path), 2 * MIB + 5)
        self.assertEqual(res.bytes, 2 * MIB + 5)
        self.assertEqual(res.sha256, hashlib.sha256(bytes(2 * MIB + 5)).hexdigest())

    def test_device_stream_reports_short_source(self):
        path = self.tmp / 'short'
        path.write_bytes(bytes(100))
        self.assertEqual(hashing.hash_device_stream(str(path), 1000).bytes, 100)

    @unittest.skipUnless(os.path.exists('/dev/zero'), 'needs /dev/zero')
    def test_real_dev_zero_small(self):
        res = hashing.hash_device_stream('/dev/zero', 8 * MIB)
        self.assertEqual(res.sha256, hashlib.sha256(bytes(8 * MIB)).hexdigest())

    def test_evict_cache_never_raises(self):
        path = self.tmp / 'f.bin'
        path.write_bytes(b'x' * 4096)
        ok, msg = hashing.evict_cache(path)
        self.assertIsInstance(ok, bool)
        self.assertTrue(msg)


if __name__ == '__main__':
    unittest.main()
