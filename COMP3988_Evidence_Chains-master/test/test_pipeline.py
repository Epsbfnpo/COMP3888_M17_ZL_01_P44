import unittest

from libevchain.exceptions import CircularEvaluationException

from libevchain.evidence_chain import EvidenceChain

from libevchain.types import (
    ALL_ARTEFACTS,
    TextType,
    DraftOf,
    Attribute,
    AttributeTypes,
)

from libevchain.pipeline import (
    ArtefactPass,
    EvidencePass,
    Pipeline,
)


class DraftOfWithAttribute(DraftOf):
    '''
        custom DraftOf generic w/ an attribute
        so that we can test observing an attribute
    '''
    relationship_type_name = "draft-of-with-attr"

    @staticmethod
    def attributes():
        return {
            'my_custom_attribute': Attribute(AttributeTypes.Any, "default value"),
        }


class SampleCommonPass(ArtefactPass):
    @staticmethod
    def evaluate(artefact):
        '''
            dummy pass that returns the hash of the
            evaluated artefact (technically not valid,
            since most mergers expect a Score as the result,
            but fine for testing)
        '''
        return artefact.artefact_hash, (), ["Sample reasoning"]


class SampleTextPassDependsOn(ArtefactPass):
    @staticmethod
    def evaluate(artefact):
        '''
            this pass depends on another pass, and is used
            to test resolution of other pass results
        '''
        score, _ = artefact.get_pass_result(SampleCommonPass)
        return score + " extra info", (), []


class SampleDraftOfTextPass(EvidencePass):
    def evaluate(evidence):
        '''
            as w/ SampleTextPass, this isn't ~strictly
            valid
        '''
        return evidence.my_custom_attribute, (), []


def custom_score_merger(final_artefact):
    artefact_results = []
    evidence_results = []

    score, _ = final_artefact.get_pass_result(SampleTextPassDependsOn)

    artefact_results.append(score)

    score, _ = final_artefact.get_pass_result(SampleCommonPass)

    artefact_results.append(score)

    for evidence_relationship in final_artefact.get_evidence():
        score, _ = evidence_relationship.get_pass_result(SampleDraftOfTextPass)
        evidence_results.append(score)

    for artefact in final_artefact.get_evidence_artefacts():
        a_res, e_res = custom_score_merger(artefact)
        artefact_results += a_res
        evidence_results += e_res

    return artefact_results, evidence_results


class CyclicalPassA(ArtefactPass):
    @staticmethod
    def evaluate(artefact):
        return artefact.get_pass_result(CyclicalPassB)


class CyclicalPassB(ArtefactPass):
    @staticmethod
    def evaluate(artefact):
        return artefact.get_pass_result(CyclicalPassA)


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
            "artefact_hash": "dummy_extra_hash",
            "artefact_type": "text",
            "evidence": [ ],
        },
        {
            "artefact_hash": "dummy_final_artefact_hash",
            "artefact_type": "text",
            "evidence": [
                {
                    "hash": "dummy_evidence_hash",
                    "relationship_type": "draft-of-with-attr.text",
                    "attributes": {
                        "my_custom_attribute": "This is my custom attribute",
                    }
                },
                {
                    "hash": "dummy_extra_hash",
                    "relationship_type": "draft-of-with-attr.text",
                },
            ],
            "attributes": {
                "language": "english",
            },
        },
    ],
}



class PipelineTests(unittest.TestCase):
    def test_run_pipeline(self):
        test_pipeline = Pipeline(
            artefact_passes = {
                ALL_ARTEFACTS: [
                    SampleCommonPass,
                ],
                TextType: [
                    SampleTextPassDependsOn,
                ],
            },
            evidence_passes = {
                DraftOfWithAttribute.text: [
                    SampleDraftOfTextPass,
                ]
            },
            score_merger = custom_score_merger
        )

        chain = EvidenceChain.from_dict(evidence_dict)

        artefact_results, evidence_results = test_pipeline.eval_chain(chain)

        self.assertEqual(set(artefact_results), {
            "dummy_final_artefact_hash",
            "dummy_evidence_hash",
            "dummy_extra_hash",
            "dummy_final_artefact_hash extra info",
            "dummy_evidence_hash extra info",
            "dummy_extra_hash extra info",
        })
        self.assertEqual(set(evidence_results), {
            "This is my custom attribute",
            "default value",
        })


    def test_run_cyclical_pipeline(self):
        '''
            run a trivially cyclical pipeline and
            ensure the cycle is caught
        '''
        cyclical_pipeline = Pipeline(
            artefact_passes = {
                ALL_ARTEFACTS: [
                    CyclicalPassA,
                    CyclicalPassB,
                ],
            },
            evidence_passes = {},
            score_merger = custom_score_merger
        )

        chain = EvidenceChain.from_dict(evidence_dict)

        with self.assertRaises(CircularEvaluationException):
            artefact_results, evidence_results = cyclical_pipeline.eval_chain(chain)



if __name__ == "__main__":
    unittest.main()
