'''
    Artefact passes process artefacts in isolation.

    They can extract information from an artefact, such as C2PA metadata,
    AI watermarks, etc.
'''

class ArtefactPass():
    '''
        An ArtefactPass is an evaluation pass that operates on a single artefact in isolation
    '''
    @staticmethod
    def evaluate(artefact):
        '''
            produce an evaluation for a given artefact

            IN:
                artefact [Artefact]
                    The artefact to evaluating

            OUT: [tuple<Score, *, list<str>>]
                a tuple consisting of;
                    - the Score for this pass
                    - an arbitrary auxiliary result (can be None) that can be used
                      to share computation between passes
                    - a list of reasoning strings (explaining why the given score
                      was emitted)
        '''
        # TODO: Make a Score class
        raise NotImplementedError(f"{type(self)} does not implement evaluate")


