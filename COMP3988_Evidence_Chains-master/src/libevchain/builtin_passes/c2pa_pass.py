'''
    Scoring values are not finalised.
'''

import c2pa
import json
from ..score import Score, Unassessed

class C2PAPass:
    """
    Extracts C2PA metadata from an Artefact object.
    """

    def extract_key_fields(self, artefact):
        """
        Read the C2PA data from an artefact and extract the top-level fields.
        """
        if artefact.has_file() == False:
            return None

        try:
            with c2pa.Reader(artefact.associated_file_path) as reader:
                manifest_store = json.loads(reader.json())

                return {
                    "c2pa_present": True,
                    "validation_state": reader.get_validation_state(),
                    "validation_results": reader.get_validation_results(),
                    "active_manifest": reader.get_active_manifest(), # Processed representation
                    "manifests": manifest_store.get("manifests", {})
                }
        except Exception as e:
            return None

    def extract_active_manifest(self, data):
        """
        Returns the active manifest.
        """
        if data is None or not data.get("c2pa_present"):
            return None
    
        return data.get("active_manifest")

    def extract_manifest_fields(self, manifest):
        """
        Extract the main three components of an active manifest.
        """
        if manifest is None:
            return None

        return {
            "claim": manifest.get("claim"),
            "assertion_store": manifest.get("assertion_store"),
            "signature": manifest.get("signature")
        }

    def evaluate(self, artefact):
        """
        Evaluate an artefact by extracting its C2PA information.

        Returns: Score object
        """

        data = self.extract_key_fields(artefact)

        if data is None or data.get("c2pa_present") == False:
            return Score()

        active_manifest = self.extract_active_manifest(data)

        if active_manifest is None:
            return Score()

        # manifest_fields = self.extract_manifest_fields(active_manifest)

        integrity = self.evaluate_integrity() # Returns Unassessed
        completeness = self.evaluate_completeness() # Returns Unassessed
        attestation_strength = self.evaluate_attestation_strength(active_manifest, data.get("validation_results"))
        ai_disclosure = self.evaluate_ai_disclosure(data.get("manifests"))

        return Score(integrity, completeness, attestation_strength, ai_disclosure)

    # https://opensource.contentauthenticity.org/docs/sdk-repos/c2pa-python/docs/working-stores


    # ========== Evaluation Methods ==========

    def evaluate_integrity(self):
        """
        Evaluate the integrity of the evidence chain using C2PA.

        """

        # Check C2PA relationships e.g. componentOf against our relationships

        return Unassessed

    def evaluate_completeness(self):
        """
        Evaluate the completeness of the evidence chain using C2PA.
        """

        return Unassessed

    def evaluate_attestation_strength(self, manifest, validation_results):
        """
        Evaluate the attestation strength of the C2PA attestation.
        """

        # Checks the manifest's signature
        # Checks success codes for if signature was successfully validated and whether its signing credential is trusted
        # Checks validation results for the manifest
            # The validation results contain checks that are successes, failures, or informational
            # Each validation item has a code and may have an explanation
    
        # Refer to: https://spec.c2pa.org/specifications/specifications/1.2/specs/C2PA_Specification.html
        

        if manifest is None or validation_results is None:
            return Unassessed

        signature = manifest.get("signature_info")
        if signature is None:
            return 0.0

        active_manifest = validation_results.get("activeManifest", {})

        successes = active_manifest.get("success", [])
        failures = active_manifest.get("failure", [])
        # informational = active_manifest.get("informational", [])

        score = 0.10 # Signature exists

        # Possible repeats
        hashed_uri_success = 0
        hashed_uri_failure = 0
        data_hash_success = 0
        data_hash_failure = 0
        bmff_hash_success = 0
        bmff_hash_failure = 0

        # Check for specific validation codes
        for result in successes:
            code = result.get("code")

            # The claim's cryptographic signature was successfully validated
            if code == "claimSignature.validated":
                score += 0.30
            # The credential used to sign the claim is trusted
            elif code == "signingCredential.trusted":
                score += 0.30

            # Hash of assertion matches hash in claim
            if code == "assertion.hashedURI.match":
                hashed_uri_success += 1
            # Hash of asset data matches declared data hash assertion
            elif code == "assertion.dataHash.match":
                data_hash_success += 1
            # Hash of BMFF asset matches declared BMFF hash assertion
            elif code == "assertion.bmffHash.match":
                bmff_hash_success += 1

        for result in failures:
            code = result.get("code")

            # Hash of assertion does not match hash in claim
            if code == "assertion.hashedURI.mismatch":
                hashed_uri_failure += 1
            # Hash of asset data does not match declared data hash assertion
            elif code == "assertion.dataHash.mismatch":
                data_hash_failure += 1
            # Hash of BMFF asset does not match declared BMFF hash assertion
            elif code == "assertion.bmffHash.mismatch":
                bmff_hash_failure += 1

        hash_scores = [] # Avoids a penalty for hashes not used like BMFF

        total_hashed_uri = hashed_uri_success + hashed_uri_failure
        if total_hashed_uri > 0:
            hash_scores.append(hashed_uri_success / total_hashed_uri)

        total_data_hash = data_hash_success + data_hash_failure
        if total_data_hash > 0:
            hash_scores.append(data_hash_success / total_data_hash)

        total_bmff_hash = bmff_hash_success + bmff_hash_failure
        if total_bmff_hash > 0:
            hash_scores.append(bmff_hash_success / total_bmff_hash)

        if hash_scores != []:
            score += 0.30 * (sum(hash_scores) / len(hash_scores))
            
        return score
        
    def evaluate_ai_disclosure(self, manifests):
        """
        Deterministically evaluate if AI involvement is disclosed in the provenance.
        """

        # Any AI disclosure is likely be found in a manifest's assertion_store's actions
        # Possible values for digitalSourceType: https://cv.iptc.org/newscodes/digitalsourcetype

        if manifests is None:
            return Unassessed

        # Check all manifests
        for manifest in manifests.values():
            assertions = manifest.get("assertions", [])

            for assertion in assertions:

                if assertion.get("label") != "c2pa.actions.v2":
                    continue
   
                actions = assertion.get("data", {}).get("actions", [])
                for action in actions:
                
                    digital_source_type = action.get("digitalSourceType")

                    # Disclosed as fully AI-generated
                    if digital_source_type == "http://cv.iptc.org/newscodes/digitalsourcetype/trainedAlgorithmicMedia":
                        return 1.0

                    # Disclosed as using AI to enhance/augment
                    elif digital_source_type == "http://cv.iptc.org/newscodes/digitalsourcetype/compositeWithTrainedAlgorithmicMedia":
                        return 0.5

        # No explicit AI disclosure
        return 0.0