'''
    A Score object will hold attributes of the final output vector.

    Score objects can combine their attribute scores in a weighted manner.
'''

# symbolic object for "score could not be assessed"
Unassessed = object()


def _mk_score_field_binary_operator(fn, keep_unassessed=False):
    '''
        function factory for making binary operators that:
        - if either score is Unassessed, return the other score
        - otherwise, return the given binary operator on the two scores
    '''
    if not keep_unassessed:
        def _score_binop(score_val_a, score_val_b):
            if score_val_a is Unassessed:
                return score_val_b
            if score_val_b is Unassessed:
                return score_val_a

            return fn(score_val_a, score_val_b)
    else:
        def _score_binop(score_val_a, score_val_b):
            if score_val_a is Unassessed:
                return Unassessed
            if score_val_b is Unassessed:
                return Unassessed

            return fn(score_val_a, score_val_b)


    return _score_binop


_add_score_field = _mk_score_field_binary_operator(lambda a, b: a + b)
_mul_score_field = _mk_score_field_binary_operator(lambda a, b: a * b, keep_unassessed=True)



class Score():
    def __init__(self, integrity: float=Unassessed, completeness: float=Unassessed, attestation_strength: float=Unassessed, ai_disclosure: float=Unassessed):
        self.integrity = integrity
        self.completeness = completeness
        self.attestation_strength = attestation_strength
        self.ai_disclosure = ai_disclosure # Can map to an ordinal scale or something similar

    def __add__(self, other):
        """
        Add to the score a single integer to every score component,
        or add another Score object component-wise.
        """
        if isinstance(other, (int, float)):
            new_integrity = _add_score_field(self.integrity, other)
            new_completeness = _add_score_field(self.completeness, other)
            new_attestation_strength = _add_score_field(self.attestation_strength, other)
            new_ai_disclosure = _add_score_field(self.ai_disclosure, other)

            new_score = Score(new_integrity, new_completeness, new_attestation_strength, new_ai_disclosure)

            return new_score

        if isinstance(other, Score):
            new_integrity = _add_score_field(self.integrity, other.integrity)
            new_completeness = _add_score_field(self.completeness, other.completeness)
            new_attestation_strength = _add_score_field(self.attestation_strength, other.attestation_strength)
            new_ai_disclosure = _add_score_field(self.ai_disclosure, other.ai_disclosure)

            new_score = Score(new_integrity, new_completeness, new_attestation_strength, new_ai_disclosure)

            return new_score

        # Neither case
        return NotImplemented

    def __mul__(self, other):
        """
        Multiply the score by either a single float or four component-wise scale factors.

        For a list of four floats, the order is: [integrity, completeness, attestation_strength, ai_disclosure]
        """
        if isinstance(other, list) and len(other) == 4:
            new_integrity = _mul_score_field(self.integrity, other[0])
            new_completeness = _mul_score_field(self.completeness, other[1])
            new_attestation_strength = _mul_score_field(self.attestation_strength, other[2])
            new_ai_disclosure = _mul_score_field(self.ai_disclosure, other[3])

            new_score = Score(new_integrity, new_completeness, new_attestation_strength, new_ai_disclosure)

            return new_score

        if isinstance(other, (int, float)):
            if other >= 0 and other <= 1:
                new_integrity = _mul_score_field(self.integrity, other)
                new_completeness = _mul_score_field(self.completeness, other)
                new_attestation_strength = _mul_score_field(self.attestation_strength, other)
                new_ai_disclosure = _mul_score_field(self.ai_disclosure, other)

                new_score = Score(new_integrity, new_completeness, new_attestation_strength, new_ai_disclosure)

                return new_score

        # Neither case
        return NotImplemented
