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



class PassExceptionKind(Enum):
    '''
        kinds of exception (refer to PassException)
    '''
    Unassessable = 1
    EvalError = 2


class PassException(Exception):
    '''
        Exception to be raised within a pass for non-fatal issues
        This should emit a kind of failure, and a reasoning message

        The failure kind is one of;
            - Unassessable (this should arise when the pass cannot determine a result eg. due to missing metadata)
            - EvalError (this should arise when a component malfunctions inside the pipeline in a non-fatal manner)
            - TODO (other kinds?)
    '''
    def __init__(self, failure_kind, reasoning):
        self.failure_kind = failure_kind
        self.reasoning = reasoning
        super().__init__(reasoning)

    def get_failure_kind(self):
        return self.failure_kind

    def get_failure_reasoning(self):
        return self.reasoning
