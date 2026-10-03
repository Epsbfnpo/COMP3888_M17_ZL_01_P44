'''
    Evidence serves as the edges in the overall evidence chain graph
'''

from libevchain.types.relationship_types import get_relationship_type
from libevchain.exceptions import MalformedJSONException, PassException, CircularEvaluationException

class Evidence():
    '''
        edge within the artefact dag
    '''
    def __init__(self, artefact, evidence_hash, relationship_type):
        '''
            IN:
                artefact [Artefact]
                    the allegedly-evidenced artefact
                    (ie. this is now an edge from the artefact matching evidence hash
                    to this artefact)

                evidence_hash [*]
                    the hash of the evidence artefact
                    this should be an actual hash object, not a string

                relationship_type [RelationshipType]
                    the relationship type for the evidence
        '''
        self.child = artefact
        self.child_hash = artefact.artefact_hash

        # we don't yet have the artefact that serves as evidence, only
        # its hash
        self.parent = None
        self.parent_hash = evidence_hash

        # map of attribute name -> value
        # supported attributes are determined by the evidence relationship type
        self._attributes = {}

        self.relationship_type = relationship_type

        # map of pass type -> pass result
        # used to cache/lazily expose pass results for pass dependencies
        self._pass_results = {}

        # map of pass type -> emitted reasoning
        # reasoning is a list of strings contextualising
        # the result computed
        self._reasoning = {}

        # callstack for passes (to detect cycles)
        self._pass_callstack = []


    def __getattr__(self, attrname):
        '''
            evidence relationships expose the attributes bound to their type
        '''
        if attrname in self.relationship_type.get_attributes():
            return self._attributes.get(
                attrname,
                self.relationship_type.get_attributes()[attrname].default,
            )

        return super().__getattribute__(attrname)



    def register_artefact(self, artefact):
        '''
            bind a given artefact as the other end (parent) of this
            evidence edge
        '''
        # should always be checked beforehand
        assert artefact.artefact_hash == self.parent_hash

        self.parent = artefact

    def get_evidence_hash(self):
        return self.parent_hash

    def get_artefact_hash(self):
        return self.child_hash


    def get_evidence_artefact(self):
        return self.parent

    def get_result_artefact(self):
        return self.child


    @staticmethod
    def from_dict(parent_artefact, json_dict):
        required_fields = { 'hash', 'relationship_type' }
        field_types = {
            'hash': str,
            'relationship_type': str,
            'attributes': dict
        }

        # basic json validation (fields and types)
        if any(key not in json_dict for key in required_fields):
            raise MalformedJSONException(f"Missing fields {required_fields.difference(json_dict.keys())}")

        for field, expected_type in field_types.items():
            field_v = json_dict.get(field, None)
            if field_v is not None and type(field_v) is not expected_type:
                raise MalformedJSONException(f"Incorrect type for field '{field}' (expected {expected_type}, got {type(field_v)})")

        evidence_hash = json_dict['hash']
        evidence_type = get_relationship_type(json_dict['relationship_type'])

        evidence = Evidence(
            parent_artefact,
            evidence_hash,
            evidence_type,
        )

        # load attributes
        evidence_attributes = json_dict.get('attributes', None)

        if evidence_attributes is not None:
            # throws AttributeException if malformed
            evidence_type.validate_attributes(evidence_attributes)
            evidence._attributes = evidence_attributes

        return evidence


    def run_passes(self):
        self._pass_results = {}

        for evidence_pass in self.relationship_type.get_passes():
            self.get_pass_result(evidence_pass)

    def get_pass_result(self, pass_type):
        if pass_type not in self.relationship_type.get_passes():
            raise Exception(f"Relationship type {self.relationship_type} does not support pass {pass_type}")

        if pass_type in self._pass_results:
            return self._pass_results[pass_type]

        if pass_type in self._pass_callstack:
            raise CircularEvaluationException(f"Pass cycle detected involving {pass_type} (callstack {self._pass_callstack})")

        self._pass_callstack.append(pass_type)
        pass_result, pass_aux, reasoning = pass_type.evaluate(self)
        self._pass_results[pass_type] = (pass_result, pass_aux)
        self._reasoning[pass_type] = reasoning

        if self._pass_callstack[-1] != pass_type:
            raise Exception(f"Corrupt pass callstack for pass {pass_type}")
        self._pass_callstack.pop()

        return self._pass_results[pass_type]


    def get_reasoning(self):
        return self._reasoning

    def get_reasoning_for(self, pass_type):
        if pass_type not in self._reasoning:
            raise Exception("No reasoning for pass {pass_type}")

        return self._reasoning[pass_type]

    def get_all_pass_results(self):
        return self._pass_results
