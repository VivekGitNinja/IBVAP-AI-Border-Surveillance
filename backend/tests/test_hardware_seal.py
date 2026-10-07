"""
Unit tests for Hardware-Bound Cryptographic Signer (Section 63 BSA 2023).
"""

import json
from pathlib import Path
import pytest

from backend.app.services.hardware_seal import (
    HardwareSecuritySeal,
    get_hardware_seal,
    seal_evidence_with_hw_attestation,
)


class TestHardwareSecuritySeal:
    def test_singleton_initialization(self):
        seal1 = get_hardware_seal()
        seal2 = get_hardware_seal()
        assert seal1 is seal2
        assert len(seal1.aik_thumbprint) == 64
        assert len(seal1.compute_pcr_composite()) == 64

    def test_seal_manifest_and_verify(self):
        signer = HardwareSecuritySeal(key_seed=b"TEST-ENCLAVE-SEED-12345")
        dummy_hash = "d576a0d057790b503046754b2d3c907fa0ceb68a44b975d045d65c36fcb9a3e6"

        seal = signer.seal_manifest(
            manifest_hash=dummy_hash,
            certifying_officer="AC Rajesh Kumar",
            officer_rank="Assistant Commandant",
            bop_sector="BOP-01 (Sector Alpha)",
        )

        assert seal["seal_version"] == "1.0"
        assert seal["statute_alignment"] == "Bharatiya Sakshya Adhiniyam, 2023 §63"
        assert seal["aik_thumbprint"] == signer.aik_thumbprint
        assert len(seal["signature"]) == 64
        assert not seal["tamper_detected"]

        # Verify legitimate seal
        is_valid = signer.verify_seal(dummy_hash, seal)
        assert is_valid is True

    def test_verify_seal_tamper_detection(self):
        signer = HardwareSecuritySeal(key_seed=b"TEST-ENCLAVE-SEED-12345")
        dummy_hash = "a" * 64
        seal = signer.seal_manifest(manifest_hash=dummy_hash)

        # 1. Tamper with manifest hash
        altered_hash = "b" * 64
        assert signer.verify_seal(altered_hash, seal) is False

        # 2. Tamper with signature
        tampered_seal = dict(seal)
        tampered_seal["signature"] = "0" * 64
        assert signer.verify_seal(dummy_hash, tampered_seal) is False

        # 3. Tamper with PCR composite inside payload
        tampered_payload_seal = dict(seal)
        tampered_payload_seal["attested_payload"] = dict(seal["attested_payload"])
        tampered_payload_seal["attested_payload"]["pcr_composite"] = "0" * 64
        assert signer.verify_seal(dummy_hash, tampered_payload_seal) is False

    def test_seal_evidence_with_hw_attestation_end_to_end(self, tmp_path, monkeypatch):
        # Point evidence directory to tmp_path
        monkeypatch.setattr("backend.app.core.config.settings.evidence_dir", str(tmp_path))

        payload = {
            "track_id": 42,
            "class": "person",
            "threat_score": 88.0,
            "notes": "Border breach detected",
        }

        path, m_hash, m_data, seal = seal_evidence_with_hw_attestation(
            incident_code="INC-2026-TEST-001",
            payload=payload,
            camera_id=1,
            camera_name="BOP Optical Gate",
            threat_score=88.0,
            certifying_officer="Insp. Vikram Singh",
        )

        assert Path(path).exists()
        assert len(m_hash) == 64
        assert m_data["incident_code"] == "INC-2026-TEST-001"
        assert seal["attested_payload"]["manifest_hash"] == m_hash
        assert seal["attested_payload"]["certifying_officer"] == "Insp. Vikram Singh"

        # Verify seal
        signer = get_hardware_seal()
        assert signer.verify_seal(m_hash, seal) is True
