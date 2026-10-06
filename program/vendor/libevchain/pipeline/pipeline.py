from libevchain.types import reset_artefact_passes, reset_relationship_passes

'''
    a pipeline describes a set of passes to apply to artefacts
    to produce a final score
'''

class Pipeline():
    '''

    '''
    def __init__(self, artefact_passes, evidence_passes, score_merger=None):
        '''
            IN:
                artefact_passes [dict<ArtefactType, list<ArtefactPass>>]
                    declarative dictionary mapping a sequence of passes
                    to an artefact type

                evidence_passes [dict<RelationshipType, list<EvidencePass>>]
                    declarative dictionary mapping a sequence of passes
                    to a relationship type

                TODO : we may need a set of weight computing functions as well

                score_merger [fn(Artefact) -> Score]
                    a function that merges the scores across all the artefacts,
                    taking the final artefact as input

            OUT: N/A
        '''
        self.artefact_passes = artefact_passes
        self.relationship_passes = evidence_passes
        self.score_merger = score_merger


    def eval_chain(self, chain):
        '''
            IN:
                chain [EvidenceChain]
                    the evidence chain to evaluate

            OUT: [Score]
                the result of evaluation
        '''
        reset_artefact_passes()
        reset_relationship_passes()

        # bind passes to artefact/relationship types
        for artefact_type, passes in self.artefact_passes.items():
            artefact_type._set_supported_passes(passes)

        for relationship_type, passes in self.relationship_passes.items():
            relationship_type._set_supported_passes(passes)

        # run standalone passes first
        for artefact in chain.artefacts.values():
            artefact.run_passes()

        # run relationship (pairwise) passes
        for ev_relationship in chain.get_evidence_relationships():
            ev_relationship.run_passes()

        # merge scores
        return self.score_merger(chain.artefacts[chain.final_artefact_hash])

