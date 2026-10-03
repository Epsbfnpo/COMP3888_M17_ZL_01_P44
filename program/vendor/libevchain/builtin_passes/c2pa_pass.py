import c2pa
import json

class C2PAPass:
    """
    Extracts C2PA metadata from an artefact.
    """

    def extract_key_fields(self, artefact):
        """
        Read the C2PA data from an artefact and extract the top-level fields."
        """
        if artefact.has_file() == False:
            return None
        
        try:
            with c2pa.Reader(artefact.associated_file_path) as reader:
                return {
                    "c2pa_present": True,
                    "validation_state": reader.get_validation_state(), # States: Trusted...
                    "validation_results": reader.get_validation_results(),
                    "active_manifest": reader.get_active_manifest
                }
        except Exception:
            return {
                "c2pa_present": False
            }

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
        """
        data = self.extract_key_fields(artefact)

        if data is None or data.get("c2pa_present") == False:
            return None

        manifest = self.extract_active_manifest(data)

        if manifest is None:
            return None

        manifest_fields = self.extract_manifest_fields(manifest)

        # TO-DO: 
        # Decide what to do with this information in the pass
        # - Check validation (how much weight to give the claim)
        # - Determine whether actions provide authorship evidence?
            # - created might suggest authorship?
        # - softwareAgents or digitalSourceType might reveal AI
        # - Who or what signed it?
        # - Score or details (json)?
        # - How to deal with all the possible cases (no guaranteed fields or common contents in attestations)

        # - AI statement
        # - How much the relationships match
        # - Get attestations
