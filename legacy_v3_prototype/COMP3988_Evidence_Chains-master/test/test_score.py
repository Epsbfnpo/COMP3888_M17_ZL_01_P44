'''
    Test score objects to ensure they compose correctly
'''

import unittest

from libevchain.score import Score, Unassessed


class TestScore(unittest.TestCase):
    def test_empty_score(self):
        # create score w/ no assessed fields
        score = Score()

        self.assertEqual(score.integrity, Unassessed)
        self.assertEqual(score.completeness, Unassessed)
        self.assertEqual(score.attestation_strength, Unassessed)
        self.assertEqual(score.ai_disclosure, Unassessed)


    def test_add_empty_scores(self):
        score_a = Score()
        score_b = Score()

        res_score = score_a + score_b

        self.assertEqual(res_score.integrity, Unassessed)
        self.assertEqual(res_score.completeness, Unassessed)
        self.assertEqual(res_score.attestation_strength, Unassessed)
        self.assertEqual(res_score.ai_disclosure, Unassessed)


    def test_mul_empty_score(self):
        score = Score()

        res_score = score * 0.5

        self.assertEqual(res_score.integrity, Unassessed)
        self.assertEqual(res_score.completeness, Unassessed)
        self.assertEqual(res_score.attestation_strength, Unassessed)
        self.assertEqual(res_score.ai_disclosure, Unassessed)

        res_score = score * [0.5, 0.25, 0.6, 0.4]

        self.assertEqual(res_score.integrity, Unassessed)
        self.assertEqual(res_score.completeness, Unassessed)
        self.assertEqual(res_score.attestation_strength, Unassessed)
        self.assertEqual(res_score.ai_disclosure, Unassessed)


    def test_add_scores(self):
        score_a = Score(0.2, 0.3, 0.4, 0.5)
        score_b = Score(0.1, -0.1, 0.7, 0.0)

        res_score = score_a + score_b

        self.assertEqual(res_score.integrity, 0.2 + 0.1)
        self.assertEqual(res_score.completeness, 0.3 + (-0.1))
        self.assertEqual(res_score.attestation_strength, 0.4 + 0.7)
        self.assertEqual(res_score.ai_disclosure, 0.5 + 0.0)

        res_score = score_a + 0.2

        self.assertEqual(res_score.integrity, 0.2 + 0.2)
        self.assertEqual(res_score.completeness, 0.3 + 0.2)
        self.assertEqual(res_score.attestation_strength, 0.4 + 0.2)
        self.assertEqual(res_score.ai_disclosure, 0.5 + 0.2)


    def test_mul_score(self):
        score = Score(0.2, 0.3, 0.4, 0.5)

        res_score = score * 0.5

        self.assertEqual(res_score.integrity, 0.2 * 0.5)
        self.assertEqual(res_score.completeness, 0.3 * 0.5)
        self.assertEqual(res_score.attestation_strength, 0.4 * 0.5)
        self.assertEqual(res_score.ai_disclosure, 0.5 * 0.5)

        res_score = score * [0.0, 0.2, 0.5, 1.0]

        self.assertEqual(res_score.integrity, 0.2 * 0.0)
        self.assertEqual(res_score.completeness, 0.3 * 0.2)
        self.assertEqual(res_score.attestation_strength, 0.4 * 0.5)
        self.assertEqual(res_score.ai_disclosure, 0.5 * 1.0)


if __name__ == "__main__":
    unittest.main()
