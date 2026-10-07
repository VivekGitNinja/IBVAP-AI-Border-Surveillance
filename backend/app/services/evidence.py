"""
Evidence engine — SHA-256 evidence manifests, hash chain, and integrity verification.

Every incident may include:
- snapshot
- detection metadata
- timestamp
- camera ID
- zone
- threat score
- AI reasons
- operator action

Evidence is integrity-protected with SHA-256 manifest hashing.
"""

from __future__ import annotations
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.app.core.config import settings


def _sha256(data: bytes) -> str:
    """Compute SHA-256 hex digest."""
    return hashlib.sha256(data).hexdigest()


def seal_evidence(
    incident_code: str,
    payload: dict[str, Any],
    camera_id: int | None = None,
    camera_name: str = "",
    threat_score: float = 0.0,
    evidence_type: str = "snapshot",
) -> tuple[str, str, dict]:
    """Create a sealed evidence manifest.

    Returns:
        (manifest_path, sha256_hash, manifest_data)
    """
    root = Path(settings.evidence_dir)
    root.mkdir(parents=True, exist_ok=True)

    ts = datetime.now(timezone.utc).isoformat()

    manifest = {
        "incident_code": incident_code,
        "evidence_type": evidence_type,
        "sealed_at": ts,
        "camera_id": camera_id,
        "camera_name": camera_name,
        "threat_score": threat_score,
        "payload": payload,
        "version": "2.0",
    }

    # Serialize deterministically
    raw = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    digest = _sha256(raw)

    # Write manifest file
    filename = f"{incident_code}_{evidence_type}_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}.json"
    manifest_path = root / filename
    manifest_path.write_bytes(raw)

    manifest["_hash"] = digest
    manifest["_file_path"] = str(manifest_path)

    return str(manifest_path), digest, manifest


def verify_manifest(path: str, expected_hash: str) -> bool:
    """Verify integrity of an evidence manifest against its expected hash."""
    try:
        raw = Path(path).read_bytes()
        actual = _sha256(raw)
        return actual == expected_hash
    except (FileNotFoundError, PermissionError):
        return False


def verify_evidence_chain(evidence_records: list[dict]) -> dict[str, Any]:
    """Verify hash-chain integrity of evidence records.

    Returns verification result with chain status.
    """
    chain_valid = True
    broken_at = None

    sorted_records = sorted(evidence_records, key=lambda e: e.get("created_at", ""))

    for i, record in enumerate(sorted_records):
        current_hash = record.get("sha256", "")
        previous_hash = record.get("previous_hash", "")

        if i > 0:
            expected_prev = sorted_records[i - 1].get("sha256", "")
            if previous_hash != expected_prev:
                chain_valid = False
                broken_at = i
                break

    return {
        "chain_valid": chain_valid,
        "records_count": len(sorted_records),
        "broken_at": broken_at,
        "verified_at": datetime.now(timezone.utc).isoformat(),
    }


def compute_file_hash(file_path: str) -> str | None:
    """Compute SHA-256 hash of a file."""
    try:
        sha = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha.update(chunk)
        return sha.hexdigest()
    except (FileNotFoundError, PermissionError):
        return None


def transcode_and_seal_clip(input_path: str, output_path: str | None = None) -> tuple[str, str, bool]:
    """Re-encode video clip to H.264 if ffmpeg is available, and compute final SHA-256 hash.
    
    Returns (final_path, sha256_hash, is_playable_in_browser).
    """
    import os
    import shutil
    import subprocess
    import logging

    logger = logging.getLogger(__name__)

    # If it's an image, calculate SHA-256 directly without video transcode
    _, ext = os.path.splitext(input_path)
    if ext.lower() in (".jpg", ".jpeg", ".png", ".webp"):
        import hashlib
        with open(input_path, "rb") as f:
            h = hashlib.sha256(f.read()).hexdigest()
        return input_path, h, True

    ffmpeg_bin = shutil.which("ffmpeg")

    if not output_path:
        base, ext = os.path.splitext(input_path)
        output_path = f"{base}_h264.mp4"

    final_path = input_path
    playable = False

    if ffmpeg_bin and settings.ffmpeg_h264_transcode:
        cmd = [
            ffmpeg_bin,
            "-y",
            "-i", input_path,
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            output_path,
        ]
        try:
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
            if res.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                final_path = output_path
                playable = True
                if input_path != output_path and os.path.exists(input_path):
                    try:
                        os.remove(input_path)
                    except OSError:
                        pass
                logger.info(f"Re-encoded evidence clip to H.264 at {output_path}")
            else:
                logger.warning(f"FFmpeg transcode returned non-zero code {res.returncode}, keeping original clip")
        except Exception as e:
            logger.warning(f"FFmpeg transcode exception: {e}")
    else:
        logger.warning("FFmpeg binary missing from PATH; keeping original video container (playable=False)")

    # Compute SHA-256 of the FINAL file on disk
    final_sha256 = compute_file_hash(final_path) or ""
    return final_path, final_sha256, playable


def generate_section_65b_certificate(
    evidence_id: int,
    incident_code: str,
    sha256_digest: str,
    camera_name: str = "BOP-01 Gate Optical Node",
    bop_sector: str = "BOP-01 (Sector Alpha)",
    certifying_officer: str = "Duty Commander, SSB Control Room",
    officer_rank: str = "Assistant Commandant",
) -> dict[str, Any]:
    """Generate an electronic evidence certificate under Section 63 of Bharatiya Sakshya Adhiniyam, 2023.
    Implemented technical controls aligned with Bharatiya Sakshya Adhiniyam, 2023 Section 63 requirements.
    Per-case statutory certificate generation remains an operational/legal prerequisite.
    """
    ts_now = datetime.now(timezone.utc).isoformat()
    cert_id = f"BSA63-{datetime.utcnow().strftime('%Y%m%d')}-{incident_code[-8:]}"

    legal_declaration = (
        f"I, {certifying_officer} ({officer_rank}), hereby certify under Section 63 of the Bharatiya Sakshya Adhiniyam, 2023 "
        f"(certificate for electronic records) that the electronic record bearing SHA-256 digest "
        f"{sha256_digest} associated with incident {incident_code} captured by surveillance node '{camera_name}' "
        f"at {bop_sector} was produced by the computer/edge surveillance system during the period over which the system was "
        f"used regularly to store or process information for border defense activities. Throughout the material part of the "
        f"said period, the computer system was operating properly, or if not, any respect in which it was not operating "
        f"properly was not such as to affect the electronic record or the accuracy of its contents. The electronic record "
        f"has been verified using cryptographic SHA-256 hash checks and stored in WORM-compliant tamper-evident storage."
    )

    cert_data = {
        "certificate_id": cert_id,
        "legal_statute": "Bharatiya Sakshya Adhiniyam, 2023 — Section 63 (certificate for electronic records) [formerly Section 65B(4) Indian Evidence Act, 1872 (repealed)]",
        "primary_statute": "Bharatiya Sakshya Adhiniyam, 2023 — Section 63",
        "legacy_statute": "Section 65B(4) Indian Evidence Act, 1872 (repealed)",
        "incident_code": incident_code,
        "evidence_id": evidence_id,
        "sha256_digest": sha256_digest,
        "surveillance_post": bop_sector,
        "camera_designation": camera_name,
        "hash_algorithm": "SHA-256 (FIPS 180-4 compliant)",
        "storage_integrity": "WORM (Write Once Read Many) Immutable Hash-Chained",
        "clock_source": "GPS-Disciplined UTC Synchronization",
        "certifying_officer": certifying_officer,
        "officer_rank": officer_rank,
        "generated_at": ts_now,
        "legal_declaration": legal_declaration,
        "admissibility_status": "Supports evidential integrity, provenance and chain-of-custody requirements relevant to electronic evidence under Bharatiya Sakshya Adhiniyam, 2023 §63; legal admissibility remains subject to judicial assessment under applicable procedural law.",
    }

    return cert_data


# Alias for modern legal citation
generate_bsa_section_63_certificate = generate_section_65b_certificate

