'''
    An evidence chain is the overall object to be evaluated by the system
'''

from libevchain.hash_methods import get_hash_method
from libevchain.exceptions import MalformedJSONException
from libevchain.artefact import Artefact
from libevchain.evidence import Evidence

class EvidenceChain():
    '''
        an EvidenceChain represents a final artefact, and
        all the evidence supporting its human authorship

        it can be loaded from and stored to json, and manipulated
    '''
    def __init__(self):
        self.artefacts = {}
        self.final_artefact_hash = None
        self.hash_method = None

        # TODO : context object or similar for tracking reasoning


    def add_artefact(self, artefact):
        '''
            add a new artefact to the chain
        '''
        if artefact.artefact_hash in self.artefacts:
            if artefact is not self.artefacts[artefact]:
                raise Exception(f"Got multiple artefacts for hash '{artefact.artefact_hash}'")
        else:
            self.artefacts[artefact.artefact_hash] = artefact


    def get_evidence_relationships(self):
        for artefact in self.artefacts.values():
            yield from artefact.evidence.values()


    @staticmethod
    def from_dict(json_dict):
        '''
            load this evidence chain for a dictionary
            (loaded from the json representation)

            IN:
                json_dict [dict]
                    a JSON-like dictionary corresponding to an
                    Evidence Chain (toplevel object in the JSON specification)

            OUT: [EvidenceChain | None]
                the loaded EvidenceChain
        '''
        required_fields = { 'hash_method', 'final_artefact_hash', 'artefacts' }
        field_types = {
            'hash_method': str,
            'final_artefact_hash': str,
            'artefacts': list,
        }
        # basic json validation (fields and types)
        if any(key not in json_dict for key in required_fields):
            raise MalformedJSONException(f"Malformed JSON: missing fields {required_fields.difference(json_dict.keys())}")

        for field, expected_type in field_types.items():
            if (actual_type := type(json_dict[field])) is not expected_type:
                raise MalformedJSONException(f"Malformed JSON: incorrect type for field '{field}' (expected {expected_type}, got {actual_type})")

        chain = EvidenceChain()

        hash_method_name = json_dict['hash_method']
        hash_method = get_hash_method(hash_method_name)
        if hash_method is None:
            # TODO : custom exception type
            raise MalformedJSONException(f"Unknown hash method '{hash_method_name}'")

        chain.hash_method = hash_method
        chain.final_artefact_hash = json_dict['final_artefact_hash']

        # add artefacts
        for artefact_dict in json_dict['artefacts']:
            chain.add_artefact(Artefact.from_dict(artefact_dict))

        # attempt to complete Artefact evidence now that we have all the
        # artefacts
        for artefact in chain.artefacts.values():
            artefact.complete_evidence(chain.artefacts)

        if chain.has_cycle(): raise MalformedJSONException("cycle in evidence chain")

        return chain


    def bind_file(self, file_path):
        '''
            given a file path, load that file and attempt
            to match it to an artefact
        '''
        file_hash = None
        # attempt to read and hash
        try:
            with open(file_path, "rb") as f:
                file_hash = self.hash_method(f)
        except Exception as e:
            raise Exception(f"Failed to open file '{file_path}': {e}")

        if file_hash not in self.artefacts:
            raise Exception(f"Hash '{file_hash}' does not match any known artefacts")

        # callthru to the artefact
        self.artefacts[file_hash].bind_file(file_path)


    def has_cycle(self, extra_edge = None):
        '''
            Check if an evidence chain contains any cycles
            Checks cycle existence by trying to create a topological
            ordering and determining if length of topological ordering is
            the same as number of artefacts in evidence chain

            extra_edge: Evidence
                Prospective edge to add to the graph
        '''
        in_degrees = {}
        out_edges = {}
        to_check = []
        visited = []

        for hash in self.artefacts:
            artefact: Artefact = self.artefacts[hash]
            in_degrees[hash] = len(artefact.evidence)

            if in_degrees[hash] == 0:
                to_check.append(hash)

            for evidence in artefact.evidence.values():
                if evidence.get_evidence_hash() not in out_edges:
                    out_edges[evidence.get_evidence_hash()] = [hash]
                else:
                    out_edges[evidence.get_evidence_hash()].append(hash)

        if extra_edge != None:
            in_degrees[extra_edge.get_artefact_hash()] += 1
            out_edges[extra_edge.get_evidence_hash()] \
                .append(extra_edge.get_artefact_hash())

        i = 0
        while i < len(to_check):
            vertex = to_check[i]
            visited.append(vertex)
            for new_vertex in out_edges.get(vertex, []):
                in_degrees[new_vertex] -= 1
                if in_degrees[new_vertex] == 0:
                    to_check.append(new_vertex)
            i += 1

        return len(visited) != len(in_degrees)
