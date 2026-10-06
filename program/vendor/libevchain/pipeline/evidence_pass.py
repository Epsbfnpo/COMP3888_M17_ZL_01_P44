'''
    Relationship passes evaluate the relationship between artefacts

    They determine the completeness and integrity of the evidence linking two artefacts
'''

class EvidencePass():
    '''
        An EvidencePass is an evaluation pass that operates on a relationship
    '''
    @staticmethod
    def evaluate(self, evidence):
        '''
            produce an evaluation for a given artefact

            IN:
                evidence [Evidence]
                    A piece of evidence (DAG edge)
                    The pass should unpack this into the parent (artefact) and child (alleged evidential
                    artefact) and operate on them

            OUT: [tuple<Score, *, list<str>>]
                a tuple consisting of:
                    - the Score for this pass
                    - an arbitrary auxiliary result (can be None) that can be used
                      to share computation between passes
                    - a list of reasoning strings (explaining why the given score
                      was emitted)
        '''
        # TODO: Make a Score class
        raise NotImplementedError(f"{type(self)} does not implement evaluate")


