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
import shutil
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


def _name(common_name):
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name),
                      x509.NameAttribute(NameOID.ORGANIZATION_NAME, "C2PA Test Fixture")])


def _usage(cert_sign):
    from cryptography import x509
    return x509.KeyUsage(digital_signature=True, content_commitment=False, key_encipherment=False,
                         data_encipherment=False, key_agreement=False, key_cert_sign=cert_sign,
                         crl_sign=cert_sign, encipher_only=False, decipher_only=False)


def _make_root(common_name="Test Root"):
    """A throwaway root CA, returned as (certificate, private key)."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    now = datetime.datetime.now(datetime.timezone.utc)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca = (x509.CertificateBuilder().subject_name(_name(common_name)).issuer_name(_name(common_name))
          .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(now - datetime.timedelta(days=1)).not_valid_after(now + datetime.timedelta(days=30))
          .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
          .add_extension(_usage(True), critical=True).sign(ca_key, hashes.SHA256()))
    return ca, ca_key


def _sign(source, target, source_type=AI_SOURCE_TYPE, root=None):
    """Sign a WAV with a digital source type claim using a leaf issued by `root` (or a new throwaway CA)."""
    import c2pa
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID

    now = datetime.datetime.now(datetime.timezone.utc)
    name, usage = _name, _usage
    ca, ca_key = root or _make_root()
    leaf_key = ec.generate_private_key(ec.SECP256R1())
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


def _claim_corrupted(signed):
    """Damage the manifest's assertion data so the SDK cannot read the manifest at all."""
    data = bytearray(Path(signed).read_bytes())
    label = data.find(b"c2pa.actions")
    assert label > 0, "actions assertion not found in the signed file"
    for index in range(label + 40, label + 56):
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
            "unreadable": _claim_corrupted(folder / "signed.wav"),
            "plain": (folder / "plain.wav").read_bytes(),
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

    def test_origin_status_for_edited_unsigned_and_unreadable_files(self):
        for label, expected in (("edited", "signature_invalid"), ("plain", "not_authenticated"),
                                ("unreadable", "not_checked")):
            response, hashes = self.evaluate(label)
            files = {row["hash"]: row for row in response["assessment_checks"]["origin_authentication"][0]["files"]}
            self.assertEqual(files[hashes[label]]["status"], expected, label)

    def test_corrupted_signing_certificate_is_reported_as_an_invalid_signature(self):
        response, _ = self.evaluate("certificate_corrupted")
        finding = self.codes(response).get("C2PA_SIGNATURE_INVALID")
        self.assertIsNotNone(finding, [row["code"] for row in response["findings"]])
        self.assertEqual(finding["severity"], "medium")
        self.assertIs(self.manifest_checks(response)[0]["passed"], False)

    def final_state(self, response):
        findings = [row for row in response["findings"] if row["code"] == "C2PA_FINAL_ATTESTATION"]
        self.assertEqual(len(findings), 1, [row["code"] for row in response["findings"]])
        return findings[0]

    def test_every_final_state_is_explained_in_one_finding(self):
        expected = {"plain": ("absent", "info", "not evidence against the submission"),
                    "signed": ("untrusted", "low", "Anyone can create such a signature"),
                    "edited": ("invalid", "medium", "the file was edited after signing"),
                    "certificate_corrupted": ("invalid", "medium", "the manifest was altered after signing"),
                    "unreadable": ("unreadable", "medium", "could not read a manifest")}
        for label, (state, severity, phrase) in expected.items():
            with self.subTest(label):
                response, _ = self.evaluate(label)
                finding = self.final_state(response)
                self.assertEqual((finding["final_c2pa_state"], finding["severity"]), (state, severity))
                self.assertIn(phrase, finding["message"])

    def test_invalid_final_state_states_its_effects(self):
        response, _ = self.evaluate("edited")
        message = self.final_state(response)["message"]
        self.assertIn("is INVALID", message)
        self.assertIn("(assertion.dataHash.mismatch)", message)
        self.assertIn("attestation strength is 0.0", message)
        self.assertIn("final_c2pa_manifest_validates", message)
        self.assertIn("not the severe cap", message)

    def test_unreadable_final_manifest_withholds_integrity_despite_supporting_evidence(self):
        response, _ = self.evaluate("unreadable", source="signed_source")
        self.assertEqual(response["validation"]["validation_state"], "manifest_not_read")
        self.assertIn("C2PA_READ_FAILED", self.codes(response))
        decision = response["assessment_checks"]["integrity_decision"][0]
        self.assertTrue(decision["qualifying_cross_evidence_assessed"])
        self.assertEqual((decision["numeric_release"], decision["release_reason"]),
                         ("withheld", "final_attestation_unreadable"))
        self.assertIsNone(response["axes"]["integrity"]["value"])

    def test_released_integrity_lists_the_facts_that_qualified_it(self):
        response, hashes = self.evaluate("signed", source="signed_source")
        decision = response["assessment_checks"]["integrity_decision"][0]
        self.assertEqual(decision["release_reason"], "qualifying_cross_evidence")
        self.assertTrue(decision["qualifying_facts"])
        self.assertTrue(all("signed_attestation" in fact["evidence_tiers"] for fact in decision["qualifying_facts"]))

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


@unittest.skipIf(importlib.util.find_spec("c2pa") is None,
                 "c2pa-python is a required dependency; see C2paRequirementTests")
class PinnedTrustListTests(unittest.TestCase):
    """Trust comes from pinned lists; a changed list is refused; the signer, not the claim, is trusted."""

    @classmethod
    def setUpClass(cls):
        from cryptography.hazmat.primitives import serialization
        cls.temp = tempfile.TemporaryDirectory()
        folder = Path(cls.temp.name)
        _tone(folder / "plain.wav")
        official_root, legacy_root = _make_root("Official Test Root"), _make_root("Legacy Test Root")
        _sign(folder / "plain.wav", folder / "official.wav", HUMAN_SOURCE_TYPE, root=official_root)
        _sign(folder / "plain.wav", folder / "legacy.wav", HUMAN_SOURCE_TYPE, root=legacy_root)
        _sign(folder / "plain.wav", folder / "unknown.wav", HUMAN_SOURCE_TYPE)
        cls.files = {name: (folder / f"{name}.wav").read_bytes() for name in ("official", "legacy", "unknown")}
        trust = folder / "trust"
        (trust / "official").mkdir(parents=True)
        (trust / "itl").mkdir()
        pem = lambda root: root[0].public_bytes(serialization.Encoding.PEM)
        contents = {"official/C2PA-TRUST-LIST.pem": pem(official_root),
                    "official/C2PA-TSA-TRUST-LIST.pem": pem(official_root),
                    "itl/anchors.pem": pem(legacy_root), "itl/allowed.pem": b"",
                    "itl/store.cfg": b"1.3.6.1.5.5.7.3.4\n"}
        for relative, data in contents.items():
            (trust / relative).write_bytes(data)
        digest = lambda relative: hashlib.sha256(contents[relative]).hexdigest()
        files = lambda mapping: {role: {"path": relative, "sha256": digest(relative)} for role, relative in mapping.items()}
        cls.manifest = {
            "official": {"label": "Test C2PA Trust List", "source": "fixture", "commit": "a" * 40,
                         "files": files({"trust_anchors": "official/C2PA-TRUST-LIST.pem",
                                         "tsa_trust_anchors": "official/C2PA-TSA-TRUST-LIST.pem"})},
            "legacy_itl": {"label": "Test ITL", "source": "fixture", "commit": "b" * 40, "frozen_on": "2026-01-01",
                           "files": files({"trust_anchors": "itl/anchors.pem", "allowed_list": "itl/allowed.pem",
                                           "trust_config": "itl/store.cfg"})}}
        cls.trust_lists = trust / "trust_lists.json"
        cls.trust_lists.write_text(json.dumps(cls.manifest))

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def evaluate(self, label, trust_lists=None):
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
        with mock.patch("legacy_parsers.c2pa_parser.TRUST_LISTS_PATH", trust_lists or self.trust_lists):
            return run.evaluate(copy.deepcopy(request), folder, "native")

    def test_signer_on_the_official_list_is_trusted(self):
        response = self.evaluate("official")
        validation, axis = response["validation"], response["axes"]["attestation_strength"]
        self.assertEqual((validation["trust_status"], validation["credential_trusted"]), ("trusted_c2pa", True))
        self.assertEqual((axis["value"], axis["availability"]), (1.0, "available"))
        self.assertEqual(validation["revocation_status"], "not_checked")
        self.assertIn("Test C2PA Trust List @ aaaaaaa", validation["trust_source"])
        self.assertIn("does not verify that its claims are true", validation["reasons"][0])
        codes = {row["code"] for row in response["findings"]}
        self.assertNotIn("C2PA_SIGNER_UNTRUSTED", codes)
        record = response["assessment_checks"]["c2pa_trust_configuration"][0]
        self.assertEqual(record["lists"]["official"]["commit"], "a" * 40)
        self.assertEqual(record["revocation_status"], "not_checked")

    def test_origin_status_names_the_trusted_and_the_unknown_signer(self):
        for label, expected in (("official", "signed_by_trusted_signer"), ("unknown", "signed_untrusted")):
            response = self.evaluate(label)
            origin = response["assessment_checks"]["origin_authentication"][0]
            self.assertEqual(origin["files"][0]["status"], expected, label)
            self.assertTrue(origin["files"][0]["c2pa_checked"])
            self.assertEqual(origin["counts"]["signed_by_trusted_signer"], int(label == "official"))
            self.assertIn(f"{int(label == 'official')} of 1 submitted files",
                          response["axes"]["integrity"]["reasoning"])

    def test_unknown_signer_scores_below_every_trusted_state(self):
        response = self.evaluate("unknown")
        validation, axis = response["validation"], response["axes"]["attestation_strength"]
        self.assertEqual((validation["trust_status"], validation["credential_trusted"]), ("untrusted", False))
        self.assertEqual((axis["value"], axis["availability"]), (0.3, "partial"))
        self.assertIn("C2PA_SIGNER_UNTRUSTED", {row["code"] for row in response["findings"]})

    def test_legacy_itl_signer_without_a_pre_freeze_signing_time_is_not_trusted(self):
        response = self.evaluate("legacy")
        self.assertEqual(response["validation"]["trust_status"], "untrusted")
        finding = {row["code"]: row for row in response["findings"]}["C2PA_SIGNER_UNTRUSTED"]
        self.assertIn("legacy Interim Trust List", finding["message"])
        self.assertIn("freeze", finding["message"])

    def test_legacy_signing_time_must_precede_the_freeze(self):
        from legacy_parsers.c2pa_parser import _signed_before
        self.assertTrue(_signed_before({"time": "2025-06-01T10:00:00Z"}, "2026-01-01"))
        self.assertFalse(_signed_before({"time": "2026-03-01T10:00:00+00:00"}, "2026-01-01"))
        self.assertFalse(_signed_before({"time": None}, "2026-01-01"))

    def test_a_changed_trust_list_file_is_refused(self):
        tampered = Path(tempfile.mkdtemp(dir=self.temp.name))
        shutil.copytree(self.trust_lists.parent, tampered, dirs_exist_ok=True)
        (tampered / "official" / "C2PA-TRUST-LIST.pem").write_bytes(b"not the pinned list")
        response = self.evaluate("official", trust_lists=tampered / "trust_lists.json")
        self.assertEqual(response["execution_status"], "error")
        self.assertIn("does not match its pinned SHA-256", response["error"])

    def test_shipped_trust_lists_match_their_pins(self):
        from legacy_parsers.c2pa_parser import TRUST_LISTS_PATH, trust_configuration_record
        record = trust_configuration_record(TRUST_LISTS_PATH)
        self.assertEqual(len(record["lists"]["official"]["commit"]), 40)
        self.assertEqual(record["lists"]["legacy_itl"]["frozen_on"], "2026-01-01")


class C2paChunkPreconditionTests(unittest.TestCase):
    """The WAV chunk list decides whether a manifest exists; the SDK decides whether it is readable."""

    def reconcile(self, reader_succeeded, present, chunks):
        from music_target.assessment_passes import _reconcile_with_c2pa_chunk
        c2pa = {"sdk_available": True, "reader_succeeded": reader_succeeded, "present": present, "warnings": []}
        wav = {"embedded_metadata": {"other_chunks": [{"chunk_id": chunk} for chunk in chunks]}}
        return _reconcile_with_c2pa_chunk(c2pa, wav)

    def test_reader_failure_without_a_c2pa_chunk_is_no_manifest(self):
        result = self.reconcile(False, False, ["LIST"])
        self.assertEqual((result["reader_succeeded"], result["present"], result["c2pa_chunk_present"]),
                         (True, False, False))
        self.assertIn("no C2PA chunk", result["warnings"][-1])

    def test_reader_failure_with_a_c2pa_chunk_stays_unreadable(self):
        result = self.reconcile(False, False, ["C2PA"])
        self.assertEqual((result["reader_succeeded"], result["c2pa_chunk_present"]), (False, True))

    def test_c2pa_chunk_without_a_readable_manifest_is_unreadable_not_absent(self):
        result = self.reconcile(True, False, ["C2PA"])
        self.assertIs(result["reader_succeeded"], False)
        self.assertIn("no readable manifest", result["warnings"][-1])

    def test_unknown_chunk_list_keeps_the_sdk_result(self):
        from music_target.assessment_passes import _reconcile_with_c2pa_chunk
        c2pa = {"sdk_available": True, "reader_succeeded": False, "present": False, "warnings": []}
        self.assertIs(_reconcile_with_c2pa_chunk(dict(c2pa), {"status": "parser_failed"})["reader_succeeded"], False)


class OriginAuthenticationTests(unittest.TestCase):
    """Per-file origin status: only an embedded, valid manifest identifies who signed a file."""

    @staticmethod
    def record(asset, source_kind="embedded", state="Trusted", trust="trusted_c2pa", present=True, **extra):
        return {"asset_hash": asset, "source_kind": source_kind, "manifest_present": present,
                "validation_state": state, "trust_status": trust, "signer": "Signer", **extra}

    def status(self, records, asset="a"):
        from music_target.assessment_passes import summarize_origin_authentication
        native = {"artefacts": [{"artefact_hash": "a", "artefact_type": "audio/master"},
                                {"artefact_hash": "doc", "artefact_type": "text/sound-report"}]}
        files = {row["hash"]: row for row in summarize_origin_authentication(native, records)[0]["files"]}
        return files[asset]

    def test_a_trusted_separate_manifest_linked_by_the_graph_does_not_authenticate(self):
        row = self.status([self.record("a", source_kind="standalone")])
        self.assertEqual(row["status"], "linked_manifest_not_verified")
        self.assertFalse(row["c2pa_checked"])

    def test_an_invalid_manifest_wins_over_a_trusted_one(self):
        records = [self.record("a"), self.record("a", state="Invalid", trust="invalid")]
        self.assertEqual(self.status(records)["status"], "signature_invalid")

    def test_known_ai_signer_and_unverified_manifests_have_their_own_status(self):
        ai = self.record("a", state="Valid", trust="untrusted", known_ai_signer="Tool")
        self.assertEqual(self.status([ai])["status"], "signed_by_known_ai_signer")
        unclear = self.record("a", state="manifest_parsed", trust=None)
        self.assertEqual(self.status([unclear])["status"], "signature_not_verified")

    def test_unchecked_is_not_reported_as_absent(self):
        missing_sdk = self.record("a", state="sdk_unavailable", trust=None, present=False)
        self.assertEqual(self.status([missing_sdk])["status"], "not_checked")
        absent = self.record("a", state="manifest_absent", trust=None, present=False)
        self.assertEqual(self.status([absent])["status"], "not_authenticated")

    def test_files_never_read_for_c2pa_are_marked_unchecked(self):
        row = self.status([self.record("a")], asset="doc")
        self.assertEqual((row["status"], row["c2pa_checked"]), ("not_authenticated", False))
