import unittest

from deck_integrity.checks.common import evaluate

GOOD = 'a' * 64
BAD = 'b' * 64
BAD2 = 'c' * 64


def obs(sha=GOOD, n=100, error=''):
    return {'sha256': None if error else sha, 'bytes': n, 'error': error, 'elapsed_s': 0.1}


class EvaluateTests(unittest.TestCase):
    def test_all_correct_passes(self):
        status, summary, stats = evaluate([obs(), obs(), obs()], GOOD, 100)
        self.assertEqual(status, 'PASS')
        self.assertIn('3/3', summary)

    def test_single_wrong_pass_fails(self):
        status, summary, stats = evaluate([obs(), obs(BAD), obs()], GOOD, 100)
        self.assertEqual(status, 'FAIL')
        self.assertEqual(stats['passes_wrong_hash'], 1)
        self.assertIn('inconsistent', summary)

    def test_consistent_but_wrong_is_distinguished(self):
        status, summary, _ = evaluate([obs(BAD), obs(BAD)], GOOD, 100)
        self.assertEqual(status, 'FAIL')
        self.assertIn('consistent across passes but different', summary)

    def test_wrong_byte_count_fails_even_with_matching_hash(self):
        status, summary, _ = evaluate([obs(), obs(n=99)], GOOD, 100)
        self.assertEqual(status, 'FAIL')
        self.assertIn('wrong number of bytes', summary)

    def test_unknown_byte_count_is_not_a_failure(self):
        self.assertEqual(evaluate([obs(n=None)], GOOD, 100)[0], 'PASS')

    def test_errors_alone_are_error_not_fail(self):
        status, _, _ = evaluate([obs(error='timed out'), obs(error='timed out')], GOOD, 100)
        self.assertEqual(status, 'ERROR')

    def test_partial_errors_with_correct_results_warn(self):
        self.assertEqual(evaluate([obs(), obs(error='exit 1')], GOOD, 100)[0], 'WARN')

    def test_errors_never_mask_a_mismatch(self):
        self.assertEqual(evaluate([obs(BAD), obs(error='exit 1')], GOOD, 100)[0], 'FAIL')

    def test_no_reference_uses_majority(self):
        status, _, stats = evaluate([obs(GOOD), obs(GOOD), obs(BAD2)], None, None)
        self.assertEqual(status, 'FAIL')
        self.assertEqual(stats['distinct_hashes'], 2)

    def test_no_observations_skip(self):
        self.assertEqual(evaluate([], GOOD, 100)[0], 'SKIP')


if __name__ == '__main__':
    unittest.main()
