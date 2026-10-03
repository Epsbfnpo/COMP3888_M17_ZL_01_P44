'''
    Artefacts serve as nodes in the evidence chain graph.

    They represent some media or production file that either is
    the final product to be evaluate, or that is evidence that supports
    its human authorship.
'''

import types

from libevchain.evidence import Evidence
from libevchain.pipeline.artefact_pass import ArtefactPass
from libevchain.types.artefact_types import get_artefact_type
from libevchain.exceptions import CircularEvaluationException, MalformedJSONException, PassException


class Artefact():
    '''
        node within the artefact dag

        associated with a local file
    '''
    def __init__(self, artefact_hash, artefact_type):
        '''
            IN:
                artefact_hash [str]
                    the hash of the artefact, as computed from some hash-method
                    as a string

                artefact_type [ArtefactType]
                    the type of the artefact

            OUT: N/A

            Note: Please don't directly call init, always build from JSON
        '''
        self.artefact_hash = artefact_hash
        self.artefact_type = artefact_type
        # map evidence hash -> evidence object
        self.evidence = {}

        # map of attribute name -> value
        # supported attributes are determined by the artefact type
        self._attributes = {}

        # the file that matches this artefact's hash
        # if None, this artefact cannot be evaluated, and
        # should negatively impact integrity
        self.associated_file_path = None

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
            artefact attributes are exposed directly as object attributes
            similarly, artefact type methods are exposed as though they were
            methods of artefacts of those types
        '''
        # defer to artefact attributes
        if attrname in self.artefact_type.get_attributes():
            return self._attributes.get(
                attrname,
                self.artefact_type.get_attributes()[attrname].default,
            )
        # defer to artefact methods (somewhat cursed)
        if attrname in (artefact_method_table := self.artefact_type.get_artefact_methods()):
            return lambda *a, **ka: artefact_method_table[attrname](self, *a, **ka)

        return super().__getattribute__(attrname)


    def has_file(self):
        return self.associated_file_path is not None

    def get_file(self):
        return self.associated_file_path

    def bind_file(self, path):
        self.associated_file_path = path

    @staticmethod
    def from_dict(json_dict):
        '''
            load this artefact from a dictionary
            (loaded from the json representation)

            IN:
                json_dict [dict]
                    a JSON-like dictonary corresponding to an
                    Artefact object (see the JSON specification)

            OUT: [Artefact]
                an incomplete Artefact object pending the binding of a file
        '''
        required_fields = { 'artefact_hash', 'artefact_type'}
        field_types = {
            'artefact_hash': str,
            'artefact_type': str,
            'evidence': list,
            'attributes': dict,
        }
        # basic json validation (fields and types)
        if any(key not in json_dict for key in required_fields):
            raise MalformedJSONException(f"Missing fields {required_fields.difference(json_dict.keys())}")

        for field, expected_type in field_types.items():
            field_v = json_dict.get(field, None)
            if field_v is not None and type(field_v) is not expected_type:
                raise MalformedJSONException(f"Incorrect type for field '{field}' (expected {expected_type}, got {type(field_v)})")


        artefact_hash = json_dict['artefact_hash']
        artefact_type = get_artefact_type(json_dict['artefact_type'])

        artefact = Artefact(
            artefact_hash,
            artefact_type,
        )

        # load attributes
        artefact_attributes = json_dict.get('attributes', None)

        if artefact_attributes is not None:
            # throws AttributeException if malformed
            artefact_type.validate_attributes(artefact_attributes)
            artefact._attributes = artefact_attributes
        # else case leaves _attributes empty

        # create incomplete evidence entries (they need to have the
        # parent artefact filled in)
        for evidence_entry in json_dict.get('evidence', []):
            ev = Evidence.from_dict(
                artefact,
                evidence_entry
            )
            evidence_hash = evidence_entry['hash']
            artefact.evidence[evidence_hash] = ev

        return artefact


    def complete_evidence(self, available_artefacts):
        '''
            given a dict <hash> -> <loaded artefact>,
            attempt to bind actual Artefact objects to this
            artefact's evidence links
        '''
        for evidence_entry in self.evidence.values():
            if evidence_entry.parent_hash not in available_artefacts:
                # TODO : custom exception
                raise Exception(f"Could not find an artefact matching evidence hash '{evidence_entry.parent_hash}'")
            evidence_entry.register_artefact(available_artefacts[evidence_entry.parent_hash])


    def run_passes(self):
        '''
            run passes bound to this artefact's type
            flushes prior pass results

            IN:
                passes [Iterable<ArtefactPass>]
                    a list of passes to run

            OUT: N/A
        '''
        self._pass_results = {}

        # run the passes on the artefact (getter runs if the result isn't
        # already computed)
        for artefact_pass in self.artefact_type.get_passes():
            self.get_pass_result(artefact_pass)


    def get_pass_result(self, pass_type):
        '''
            attempts to get the result of a given pass
            on this artefact (running it if it hasn't already been run)

            detects cyclical dependencies with a callstack

            populates result and reasoning, returns the result
            as (score_object, auxiliary data)
        '''
        if pass_type not in self.artefact_type.get_passes():
            raise Exception(f"Artefact type {self.artefact_type} does not support pass {pass_type}")

        if pass_type in self._pass_results:
            # expose already executed passes
            return self._pass_results[pass_type]

        if pass_type in self._pass_callstack:
            raise CircularEvaluationException(f"Pass cycle detected involving {pass_type} (callstack {self._pass_callstack})")

        # otherwise, execute pass
        self._pass_callstack.append(pass_type)
        pass_result, pass_aux, reasoning = pass_type.evaluate(self)
        self._pass_results[pass_type] = (pass_result, pass_aux)
        self._reasoning[pass_type] = reasoning

        if self._pass_callstack[-1] != pass_type:
            raise Exception(f"Corrupt pass callstack for pass {pass_type}")
        self._pass_callstack.pop()

        return self._pass_results[pass_type]

    def get_reasoning(self):
        '''
            return the entire reasoning dict
            useful for displaying all reasoning nicely
        '''
        return self._reasoning

    def get_reasoning_for(self, pass_type):
        '''
            get reasoning for a specific pass type
            does not resolve un-executed passes (errors
            if the pass cannot be found)
        '''
        if pass_type not in self._reasoning:
            raise Exception(f"No reasoning for pass {pass_type}")

        return self._reasoning[pass_type]


    def get_all_pass_results(self):
        return self._pass_results


    def get_evidence(self):
        for evidence in self.evidence.values():
            yield evidence


    def get_evidence_artefacts(self):
        for evidence in self.evidence.values():
            yield evidence.get_evidence_artefact()
