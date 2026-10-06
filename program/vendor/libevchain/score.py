'''
    A Score object will hold attributes of the final output vector.

    Score objects can combine their attribute scores in a weighted manner.
'''

class Score():
    def __init__(self, integrity: float, completeness: float, attestation_strength: float , ai_disclosure: float):
        self.integrity = integrity
        self.completeness = completeness
        self.attestation_strength = attestation_strength
        self.ai_disclosure = ai_disclosure # Can map to an ordinal scale or something similar

    def __add__(self, other):
        """
        Add to the score a single integer to every score component,
        or add another Score object component-wise.
        """
        if isinstance(other, int):
            new_integrity = self.integrity + other
            new_completeness = self.completeness + other
            new_attestation_strength = self.attestation_strength + other
            new_ai_disclosure = self.ai_disclosure + other

            new_score = Score(new_integrity, new_completeness, new_attestation_strength, new_ai_disclosure)

            return new_score

        if isinstance(other, Score):
            new_integrity = self.integrity + other.integrity
            new_completeness = self.completeness + other.completeness
            new_attestation_strength = self.attestation_strength + other.attestation_strength
            new_ai_disclosure = self.ai_disclosure + other.ai_disclosure

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
            new_integrity = self.integrity * other[0]
            new_completeness = self.completeness * other[1]
            new_attestation_strength = self.attestation_strength * other[2]
            new_ai_disclosure = self.ai_disclosure * other[3]

            new_score = Score(new_integrity, new_completeness, new_attestation_strength, new_ai_disclosure)

            return new_score

        if isinstance(other, float):
            if other >= 0 and other <= 1:
                new_integrity = self.integrity * other
                new_completeness = self.completeness * other
                new_attestation_strength = self.attestation_strength * other
                new_ai_disclosure = self.ai_disclosure * other
    
                new_score = Score(new_integrity, new_completeness, new_attestation_strength, new_ai_disclosure)
    
                return new_score

        # Neither case
        return NotImplemented