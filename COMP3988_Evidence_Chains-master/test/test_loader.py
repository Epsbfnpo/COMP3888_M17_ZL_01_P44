'''
    Tests loading Evidence Chains from JSON dictionaries
'''

import unittest

from libevchain.evidence_chain import EvidenceChain


class LoadTests(unittest.TestCase):
    def test_valid_load(self):
        evidence_dict = {
            "hash_method": "sha256",
            "final_artefact_hash": "dummy_final_artefact_hash",
            "artefacts": [
                {
                    "artefact_hash": "dummy_evidence_hash",
                    "artefact_type": "text",
                    "evidence": [ ],
                },
                {
                    "artefact_hash": "dummy_final_artefact_hash",
                    "artefact_type": "text",
                    "evidence": [
                        {
                            "hash": "dummy_evidence_hash",
                            "relationship_type": "draft-of.text",
                        }
                    ],
                    "attributes": {
                        "language": "english",
                    },
                },
            ],
        }

        # try to load evidence_dict
        chain = EvidenceChain.from_dict(evidence_dict)


        self.assertEqual(chain.artefacts.keys(), { "dummy_final_artefact_hash", "dummy_evidence_hash" })

        final_artefact = chain.artefacts["dummy_final_artefact_hash"]
        self.assertEqual(final_artefact.language, "english")
        self.assertEqual(final_artefact.evidence.keys(), { "dummy_evidence_hash" })


        evidence_artefact = chain.artefacts["dummy_evidence_hash"]
        self.assertEqual(evidence_artefact.language, None)
        self.assertEqual(evidence_artefact.evidence, dict())


    def test_load_incomplete(self):
        pass
        # TODO : load soemthing where there is a non-existent evidence hash

    def test_load_no_final(self):
        pass
        # TODO : load something where there is no artefact matching the final-artefact

    def test_load_cycle(self):
        pass
        # TODO : load something where there is an evidence cycle and detect it


if __name__ == "__main__":
    unittest.main()
