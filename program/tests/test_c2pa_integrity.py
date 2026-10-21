"""C2PA is a required dependency, and a failed manifest is visible and affects Integrity.

Signed fixtures are generated at test time with a throwaway certificate chain,
so no third-party audio or private keys are stored in the repository.
"""

import base64
import copy
import datetime
import hashlib
import importlib.util
import json
import struct
import sys
import tempfile
import unittest
import wave
from pathlib import Path

import numpy as np

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

import run  # noqa: E402

AI_SOURCE_TYPE = "http://cv.iptc.org/newscodes/digitalsourcetype/trainedAlgorithmicMedia"
HUMAN_SOURCE_TYPE = "http://cv.iptc.org/newscodes/digitalsourcetype/digitalCapture"


class C2paRequirementTests(unittest.TestCase):
    def test_c2pa_python_is_pinned_as_a_core_requirement(self):
        lines = (BASE / "requirements.txt").read_text().splitlines()
        self.assertIn("c2pa-python==0.37.10", [line.strip() for line in lines])

    def test_c2pa_python_is_installed(self):
        self.assertIsNotNone(importlib.util.find_spec("c2pa"),
                             "c2pa-python is a required dependency; run pip install -r requirements.txt")


def _chunks(data):
    out, index = [], 12
    while index + 8 <= len(data):
        cid = data[index:index + 4]
        size = struct.unpack("<I", data[index + 4:index + 8])[0]
        out.append((cid, data[index + 8:index + 8 + size]))
        index += 8 + size + (size & 1)
    return out


def _riff(chunks):
    body = b"WAVE"
    for cid, payload in chunks:
        body += cid + struct.pack("<I", len(payload)) + payload + (b"\0" if len(payload) & 1 else b"")
    return b"RIFF" + struct.pack("<I", len(body)) + body


def _tone(path, seconds=1.0, rate=8000, frequency=440.0):
    t = np.arange(int(seconds * rate)) / rate
    samples = (0.3 * np.sin(2 * np.pi * frequency * t) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as stream:
        stream.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        stream.writeframes(samples.tobytes())


def _sign(source, target, source_type=AI_SOURCE_TYPE):
    """Sign a WAV with a digital source type claim using a throwaway CA and leaf."""
    import c2pa
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    now = datetime.datetime.now(datetime.timezone.utc)

    def name(common_name):
        return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name),
                          x509.NameAttribute(NameOID.ORGANIZATION_NAME, "C2PA Test Fixture")])

    def usage(cert_sign):
        return x509.KeyUsage(digital_signature=True, content_commitment=False, key_encipherment=False,
                             data_encipherment=False, key_agreement=False, key_cert_sign=cert_sign,
                             crl_sign=cert_sign, encipher_only=False, decipher_only=False)

    ca_key, leaf_key = ec.generate_private_key(ec.SECP256R1()), ec.generate_private_key(ec.SECP256R1())
    ca = (x509.CertificateBuilder().subject_name(name("Test Root")).issuer_name(name("Test Root"))
          .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(now - datetime.timedelta(days=1)).not_valid_after(now + datetime.timedelta(days=30))
          .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
          .add_extension(usage(True), critical=True).sign(ca_key, hashes.SHA256()))
    leaf = (x509.CertificateBuilder().subject_name(name("Test Signer")).issuer_name(ca.subject)
            .public_key(leaf_key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1)).not_valid_after(now + datetime.timedelta(days=30))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(usage(False), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.EMAIL_PROTECTION]), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(leaf_key.public_key()), critical=False)
            .sign(ca_key, hashes.SHA256()))
    chain = leaf.public_bytes(serialization.Encoding.PEM) + ca.public_bytes(serialization.Encoding.PEM)
    key = leaf_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                 serialization.NoEncryption())
    manifest = {"claim_generator_info": [{"name": "c2pa-test-fixture", "version": "1.0"}],
                "title": "fixture.wav", "format": "audio/wav",
                "assertions": [{"label": "c2pa.actions", "data": {"actions": [
                    {"action": "c2pa.created", "digitalSourceType": source_type}]}}]}
    signer = c2pa.Signer.from_info(c2pa.C2paSignerInfo(alg=b"es256", sign_cert=chain, private_key=key, ta_url=None))
    with open(source, "rb") as src, open(target, "w+b") as dst:
        c2pa.Builder(json.dumps(manifest)).sign(signer, "audio/wav", src, dst)
    return leaf.public_bytes(serialization.Encoding.DER), str(leaf.serial_number)


def _edited_after_signing(signed):
    """Change the audio but keep the original C2PA chunk, as an editor might."""
    chunks = []
    for cid, payload in _chunks(Path(signed).read_bytes()):
        if cid == b"data":
            samples = np.frombuffer(payload, dtype="<i2") // 2
            payload = samples.astype("<i2").tobytes()
        chunks.append((cid, payload))
    return _riff(chunks)


def _certificate_corrupted(signed, leaf_der):
    data = bytearray(Path(signed).read_bytes())
    position = data.find(leaf_der)
    assert position > 0, "signing certificate not found in the signed file"
    middle = position + len(leaf_der) // 2
    for index in range(middle, middle + 8):
        data[index] ^= 0xFF
    return bytes(data)


@unittest.skipIf(importlib.util.find_spec("c2pa") is None,
                 "c2pa-python is a required dependency; see C2paRequirementTests")
class C2paValidationFailureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        folder = Path(cls.temp.name)
        _tone(folder / "plain.wav")
        _tone(folder / "plain-source.wav", frequency=330.0)
        cls.leaf, _ = _sign(folder / "plain.wav", folder / "signed.wav")
        _sign(folder / "plain-source.wav", folder / "signed-source.wav")
        cls.files = {
            "signed": (folder / "signed.wav").read_bytes(),
            "signed_source": (folder / "signed-source.wav").read_bytes(),
            "edited": _edited_after_signing(folder / "signed.wav"),
            "edited_source": _edited_after_signing(folder / "signed-source.wav"),
            "certificate_corrupted": _certificate_corrupted(folder / "signed.wav", cls.leaf),
        }

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def evaluate(self, final, source=None):
        folder = Path(tempfile.mkdtemp(dir=self.temp.name))
        (folder / "objects").mkdir()
        hashes = {}
        for label in filter(None, (final, source)):
            digest = hashlib.sha256(self.files[label]).hexdigest()
            (folder / "objects" / f"{digest}.wav").write_bytes(self.files[label])
            hashes[label] = digest
        request = json.loads((BASE / "examples/valid-generated/request.json").read_text())
        request["workflow"]["workflow_id"] = "ai_assisted_or_generated_production"
        request["workflow"]["modifiers"] = ["includes_ai_generated_audio"]
        artefacts = []
        if source:
            artefacts.append({"artefact_hash": hashes[source], "artefact_type": "audio/ai-source",
                              "attributes": {"creation_method": "ai-generated"}, "evidence": []})
        artefacts.append({"artefact_hash": hashes[final], "artefact_type": "audio/master",
                          "attributes": {"creation_method": "ai-generated"},
                          "evidence": ([{"hash": hashes[source], "relationship_type": "derived_from",
                                         "attributes": {}}] if source else [])})
        request["chain"]["artefacts"] = artefacts
        request["chain"]["final_artefact_hash"] = hashes[final]
        request["objects"] = {digest: f"objects/{digest}.wav" for digest in hashes.values()}
        response = run.evaluate(copy.deepcopy(request), folder, "native")
        self.assertEqual(response["execution_status"], "succeeded", response.get("error"))
        return response, hashes

    @staticmethod
    def codes(response):
        return {row["code"]: row for row in response["findings"]}

    @staticmethod
    def manifest_checks(response):
        return [row for row in response["assessment_checks"]["cross_evidence"]
                if row["rule"] == "final_c2pa_manifest_validates"]

    def test_valid_manifest_reports_no_failure(self):
        response, _ = self.evaluate("signed")
        self.assertEqual(response["validation"]["validation_state"], "Valid")
        self.assertFalse({"C2PA_CONTENT_BINDING_FAILED", "C2PA_SIGNATURE_INVALID",
                          "C2PA_VALIDATION_FAILED"} & set(self.codes(response)))
        self.assertEqual(self.manifest_checks(response), [])

    def test_audio_edited_after_signing_is_a_visible_ordinary_contradiction(self):
        response, hashes = self.evaluate("edited")
        finding = self.codes(response).get("C2PA_CONTENT_BINDING_FAILED")
        self.assertIsNotNone(finding, [row["code"] for row in response["findings"]])
        self.assertEqual((finding["severity"], finding["scope"], finding["evidence_hash"]),
                         ("medium", "final", hashes["edited"]))
        self.assertIn("assertion.dataHash.mismatch", finding["validation_codes"])
        self.assertNotIn("signingCredential.untrusted", finding["validation_codes"])
        self.assertEqual(response["validation"]["validation_state"], "Invalid")
        self.assertIn("failed SDK validation", response["validation"]["reasons"][0])
        checks = self.manifest_checks(response)
        self.assertEqual(len(checks), 1)
        self.assertIs(checks[0]["passed"], False)
        decision = response["assessment_checks"]["integrity_decision"][0]
        self.assertTrue(decision["contradiction_present"])
        self.assertFalse(decision["severe_contradiction_present"])
        self.assertIn("CROSS_EVIDENCE_CONTRADICTION", self.codes(response))

    def test_corrupted_signing_certificate_is_reported_as_an_invalid_signature(self):
        response, _ = self.evaluate("certificate_corrupted")
        finding = self.codes(response).get("C2PA_SIGNATURE_INVALID")
        self.assertIsNotNone(finding, [row["code"] for row in response["findings"]])
        self.assertEqual(finding["severity"], "medium")
        self.assertIs(self.manifest_checks(response)[0]["passed"], False)

    def test_invalid_supporting_manifest_is_reported_without_the_final_fact(self):
        response, hashes = self.evaluate("signed", source="edited_source")
        finding = self.codes(response).get("C2PA_CONTENT_BINDING_FAILED")
        self.assertIsNotNone(finding, [row["code"] for row in response["findings"]])
        self.assertEqual((finding["severity"], finding["scope"], finding["evidence_hash"]),
                         ("low", "supporting", hashes["edited_source"]))
        self.assertEqual(self.manifest_checks(response), [])


if __name__ == "__main__":
    unittest.main()


@unittest.skipIf(importlib.util.find_spec("c2pa") is None,
                 "c2pa-python is a required dependency; see C2paRequirementTests")
class AsymmetricC2paTrustTests(unittest.TestCase):
    """An AI label is believed; a signature supporting human authorship needs a trusted signer."""

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        folder = Path(cls.temp.name)
        _tone(folder / "plain.wav")
        _sign(folder / "plain.wav", folder / "human.wav", HUMAN_SOURCE_TYPE)
        _sign(folder / "plain.wav", folder / "ai.wav", AI_SOURCE_TYPE)
        # A known AI generation tool whose manifest happens to carry no AI source type.
        leaf, serial = _sign(folder / "plain.wav", folder / "known.wav", HUMAN_SOURCE_TYPE)
        cls.files = {name: (folder / f"{name}.wav").read_bytes() for name in ("human", "ai", "known")}
        cls.files["known_edited"] = _edited_after_signing(folder / "known.wav")
        cls.known_list = folder / "known_ai_signers.json"
        cls.known_list.write_text(json.dumps({"signers": [{
            "name": "Fixture AI Tool",
            "fingerprint_sha256_base64": base64.b64encode(hashlib.sha256(leaf).digest()).decode(),
            "certificate_serial_number": serial}]}))

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def evaluate(self, label):
        from unittest import mock
        folder = Path(tempfile.mkdtemp(dir=self.temp.name))
        (folder / "objects").mkdir()
        digest = hashlib.sha256(self.files[label]).hexdigest()
        (folder / "objects" / f"{digest}.wav").write_bytes(self.files[label])
        request = json.loads((BASE / "examples/valid-generated/request.json").read_text())
        request["workflow"]["workflow_id"] = "recorded_single_performance_to_final"
        request["workflow"]["modifiers"] = []
        request["chain"]["artefacts"] = [{"artefact_hash": digest, "artefact_type": "audio/master",
                                          "attributes": {"creation_method": "recorded"}, "evidence": []}]
        request["chain"]["final_artefact_hash"] = digest
        request["objects"] = {digest: f"objects/{digest}.wav"}
        with mock.patch("legacy_parsers.c2pa_parser.KNOWN_AI_SIGNERS_PATH", self.known_list):
            response = run.evaluate(copy.deepcopy(request), folder, "native")
        self.assertEqual(response["execution_status"], "succeeded", response.get("error"))
        codes = {row["code"]: row for row in response["findings"]}
        ai_checks = [row for row in response["assessment_checks"]["cross_evidence"]
                     if row["rule"] == "c2pa_ai_source_type_matches_creation_method"]
        return response, codes, ai_checks

    def test_self_signed_human_authorship_claim_cannot_release_integrity(self):
        response, codes, ai_checks = self.evaluate("human")
        self.assertEqual(codes["C2PA_SIGNER_UNTRUSTED"]["severity"], "medium")
        self.assertNotIn("C2PA_KNOWN_AI_SIGNER", codes)
        self.assertEqual([(row["passed"], row["evidence_tier"]) for row in ai_checks],
                         [(True, "submitter_document")])
        self.assertIsNone(response["axes"]["integrity"]["value"])

    def test_signed_ai_label_contradicts_a_recorded_declaration(self):
        response, codes, ai_checks = self.evaluate("ai")
        self.assertNotIn("C2PA_SIGNER_UNTRUSTED", codes)
        self.assertEqual([(row["passed"], row["evidence_tier"]) for row in ai_checks],
                         [(False, "signed_attestation")])
        self.assertTrue(response["assessment_checks"]["integrity_decision"][0]["contradiction_present"])

    def test_known_ai_signer_is_believed_without_an_ai_source_type(self):
        response, codes, ai_checks = self.evaluate("known")
        finding = codes["C2PA_KNOWN_AI_SIGNER"]
        self.assertEqual((finding["severity"], finding["known_ai_signer"]), ("medium", "Fixture AI Tool"))
        self.assertNotIn("C2PA_SIGNER_UNTRUSTED", codes)
        self.assertEqual([(row["passed"], row["evidence_tier"], row["known_ai_signer"]) for row in ai_checks],
                         [(False, "signed_attestation", "Fixture AI Tool")])
        records = response["assessment_checks"]["c2pa_attestation_records"]
        self.assertEqual(records[0]["known_ai_signer"], "Fixture AI Tool")

    def test_known_ai_signer_is_still_recognised_after_editing(self):
        response, codes, ai_checks = self.evaluate("known_edited")
        self.assertIn("C2PA_KNOWN_AI_SIGNER", codes)
        self.assertIn("C2PA_CONTENT_BINDING_FAILED", codes)
        self.assertIs(ai_checks[0]["passed"], False)
