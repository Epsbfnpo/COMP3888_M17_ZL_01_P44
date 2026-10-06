'''
    Custom exceptions

    Includes:
    - MalformedJSONException
'''
from enum import Enum

class CircularEvaluationException(Exception):
    '''
        Exception for circular dependencies in evaluation passes
    '''
    pass

class MalformedJSONException(Exception):
    '''
        Exception for malformed JSONs in evidence or artefact construction
    '''
    pass


class AttributeException(Exception):
    '''
        Exception raised by ArtefactType.validate_attributes
    '''
    pass



class PassException(Exception):
    '''
        Exception to be raised within a pass for non-fatal issues
        In this case, the entire score object is replaced with Unassessed,
        so that the chain evaluation can proceed

        Should be initialised with reasoning, explaining why the pass failed
    '''
    def __init__(self, reasoning):
        self.reasoning = reasoning
        super().__init__(reasoning)

    def get_failure_reasoning(self):
        return self.reasoning
