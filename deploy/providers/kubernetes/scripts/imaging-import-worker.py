#!/usr/bin/env python3
"""
FLIP Imaging Import Worker (DQR Bypass)

Bypasses the XNAT DQR plugin (which throws PacsNotStorableException on the
K8s trust) by directly performing C-MOVE operations from Orthanc (PACS) to
XNAT over DICOM using DCMTK command-line tools.

Why DCMTK instead of pynetdicom?
  - pynetdicom's C-MOVE sends a request to Orthanc which Orthanc then aborts
    (DUL service-user abort). The root cause is unknown - possibly a negotiation
    issue between pynetdicom and the OFFIS DCMTK library embedded in Orthanc.
  - DCMTK's movescu tool works perfectly and has been verified end-to-end.

Designed to run as a Kubernetes Job (or one-off on the dcmtk pod).

Workflow:
  1. Connect to the xnat-db via service DNS (host=xnat-db, port=5432).
  2. Read all QUEUED entries from xhbm_queued_pacs_request.
  3. For each:
     a. Use DCMTK findscu to C-FIND Orthanc for the accession number
     b. Use DCMTK movescu to C-MOVE the study from Orthanc to XNAT
     c. Update the XNAT DB to reflect the outcome

Environment variables:
  XNAT_DB_HOST     - XNAT PostgreSQL host (default: xnat-db)
  XNAT_DB_PORT     - XNAT PostgreSQL port (default: 5432)
  XNAT_DB_USER     - XNAT PostgreSQL user (default: xnat)
  XNAT_DB_PASSWORD - XNAT PostgreSQL password
  XNAT_DB_NAME     - XNAT PostgreSQL database (default: xnat)
  ORTHANC_HOST     - Orthanc DICOM host (default: orthanc)
  ORTHANC_PORT     - Orthanc DICOM port (default: 4242)
  ORTHANC_AET      - Orthanc AE Title (default: ORTHANC)
  XNAT_AET         - XNAT AE Title (default: XNAT)
  XNAT_DICOM_HOST  - XNAT DICOM SCP host (default: xnat-web)
  XNAT_DICOM_PORT  - XNAT DICOM SCP port (default: 8104)
  WORKER_AET       - This worker's AE Title (default: FLIPIMPORT)
  BATCH_SIZE       - Max studies to process per run (default: 50)
  POLL_INTERVAL    - Seconds between polls when running as daemon (default: 300)
  RUN_ONCE         - If "true", process one batch and exit (default: true)
  LOG_LEVEL        - Logging level (default: INFO)
  XNAT_URL         - XNAT web URL (default: http://xnat-web:8080)
  XNAT_ADMIN_USER  - XNAT admin username (default: admin)
  XNAT_ADMIN_PASSWORD - XNAT admin password
  PREARCHIVE_PATH  - XNAT prearchive filesystem path (default: /data/xnat/prearchive)
  PREARCHIVE_POLL_SECS - Seconds to wait for prearchive data after C-MOVE (default: 10)
"""

import base64
import json
import logging
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

import psycopg2
import psycopg2.extras

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_port(value: str, default: int = 5432) -> int:
    """Extract port from either a plain integer string or a K8s-style
    "tcp://host:port" URL injected by service environment variables."""
    if value.startswith("tcp://"):
        return int(value.rsplit(":", 1)[-1])
    return int(value)


def _extract_host(value: str, default: str = "localhost") -> str:
    """Extract host from either a plain hostname or a K8s-style
    "tcp://host:port" URL."""
    if value.startswith("tcp://"):
        return value.split("://", 1)[1].rsplit(":", 1)[0]
    if value.startswith("http://"):
        return value.split("://", 1)[1].rsplit(":", 1)[0]
    return value


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

logger = logging.getLogger("imaging-import-worker")


@dataclass
class Config:
    xnat_db_host: str = _extract_host(os.environ.get("XNAT_DB_HOST", "xnat-db"))
    xnat_db_port: int = _extract_port(os.environ.get("XNAT_DB_PORT", "5432"))
    xnat_db_user: str = os.environ.get("XNAT_DB_USER", "xnat")
    xnat_db_password: str = os.environ.get("XNAT_DB_PASSWORD", "")
    xnat_db_name: str = os.environ.get("XNAT_DB_NAME", "xnat")

    orthanc_host: str = _extract_host(os.environ.get("ORTHANC_HOST", "orthanc"))
    orthanc_port: int = _extract_port(os.environ.get("ORTHANC_PORT", "4242"))
    orthanc_aet: str = os.environ.get("ORTHANC_AET", "ORTHANC")

    xnat_aet: str = os.environ.get("XNAT_AET", "XNAT")
    xnat_dicom_host: str = os.environ.get("XNAT_DICOM_HOST", "xnat-web")
    xnat_dicom_port: int = _extract_port(os.environ.get("XNAT_DICOM_PORT", "8104"))

    worker_aet: str = os.environ.get("WORKER_AET", "FLIPIMPORT")

    batch_size: int = int(os.environ.get("BATCH_SIZE", "50"))
    poll_interval: int = int(os.environ.get("POLL_INTERVAL", "300"))
    run_once: bool = os.environ.get("RUN_ONCE", "true").lower() == "true"

    pacs_id: int = int(os.environ.get("PACS_ID", "1"))

    # Prearchive building
    xnat_url: str = os.environ.get("XNAT_URL", "http://xnat-web:8080")
    xnat_admin_user: str = os.environ.get("XNAT_ADMIN_USER", "admin")
    xnat_admin_password: str = os.environ.get("XNAT_ADMIN_PASSWORD", "")
    prearchive_path: str = os.environ.get("PREARCHIVE_PATH", "/data/xnat/prearchive")
    prearchive_poll_secs: int = int(os.environ.get("PREARCHIVE_POLL_SECS", "10"))


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def get_db_connection(cfg: Config):
    """Connect to the XNAT PostgreSQL database."""
    conn = psycopg2.connect(
        host=cfg.xnat_db_host,
        port=cfg.xnat_db_port,
        user=cfg.xnat_db_user,
        password=cfg.xnat_db_password,
        dbname=cfg.xnat_db_name,
    )
    conn.autocommit = True
    return conn


def fetch_queued_requests(conn, cfg: Config, batch_size: int | None = None) -> list[dict]:
    """Fetch QUEUED PACS import requests from the XNAT DB."""
    limit = batch_size or cfg.batch_size
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """SELECT id, accession_number, study_instance_uid, xnat_project,
                      patient_id, patient_name, study_date, study_id
               FROM xhbm_queued_pacs_request
               WHERE status = 'QUEUED'
                 AND enabled = true
                 AND (disabled IS NULL OR disabled = 'infinity' OR disabled = '1970-01-01 00:00:00')
               ORDER BY created ASC
               LIMIT %s
            """,
            (limit,),
        )
        rows = cur.fetchall()
        return [dict(r) for r in rows]


def mark_request_completed(conn, request_id: int, study_uid: str | None = None):
    """Move a queued request to the executed_pacs_request table."""
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO xhbm_executed_pacs_request
               (created, accession_number, status, xnat_project, timestamp,
                disabled, enabled, destination_ae_title, error_message,
                pacs_id, patient_id, patient_name, priority, queued_time,
                remapping_script, request_id, study_date, study_id,
                study_instance_uid, username, executed_time)
               SELECT created, accession_number, 'COMPLETED', xnat_project, NOW(),
                      '1970-01-01 00:00:00', false, destination_ae_title, NULL,
                      pacs_id, patient_id, patient_name, priority, queued_time,
                      remapping_script, request_id, study_date, study_id,
                      %s, username, NOW()
               FROM xhbm_queued_pacs_request
               WHERE id = %s
            """,
            (study_uid or "", request_id),
        )
        # Delete child rows first (xhbm_queued_pacs_request_series_ids)
        cur.execute(
            "DELETE FROM xhbm_queued_pacs_request_series_ids WHERE queued_pacs_request = %s",
            (request_id,),
        )
        cur.execute("DELETE FROM xhbm_queued_pacs_request WHERE id = %s", (request_id,))
    logger.info("Request %d marked as COMPLETED (study UID: %s)", request_id, study_uid or "N/A")


def mark_request_failed(conn, request_id: int, error_message: str):
    """Mark a queued request as failed."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE xhbm_queued_pacs_request SET status = 'FAILED', error_message = %s WHERE id = %s",
            (error_message[:500], request_id),
        )
    logger.warning("Request %d marked as FAILED: %s", request_id, error_message[:120])


def cleanup_dqr_failures(conn, accession_number: str):
    """Clean up DQR plugin failures for an accession number."""
    with conn.cursor() as cur:
        cur.execute(
            """DELETE FROM xhbm_executed_pacs_request_series_ids
               WHERE executed_pacs_request IN (
                   SELECT id FROM xhbm_executed_pacs_request
                   WHERE accession_number = %s
               )""",
            (accession_number,),
        )
        cur.execute(
            "DELETE FROM xhbm_executed_pacs_request WHERE accession_number = %s",
            (accession_number,),
        )
        deleted = cur.rowcount
        if deleted:
            logger.info("Cleaned up %d DQR failure(s) for accession %s", deleted, accession_number)


def _get_working_project(conn) -> str | None:
    """Find a FLIP project for fallback experiment builds."""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT p.id FROM xnat_projectdata p
               JOIN xnat_experimentdata e ON e.project = p.id
               WHERE p.name LIKE '%FLIP%' OR p.name LIKE '%ECS%' OR p.name LIKE '%K8s%'
               ORDER BY p.id ASC LIMIT 1"""
        )
        row = cur.fetchone()
        return row[0] if row else None


def move_experiment_via_db(conn, source_project: str, target_project: str, label: str) -> bool:
    """Move experiment between projects via direct DB update (fallback when build API fails)."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE xnat_experimentdata SET project = %s WHERE project = %s AND label = %s",
            (target_project, source_project, label),
        )
        if cur.rowcount:
            logger.info("Moved experiment %s from %s to %s", label, source_project, target_project)
            return True
        logger.warning("No experiment %s found in source project %s", label, source_project)
        return False


# ---------------------------------------------------------------------------
# DICOM C-FIND - find study by accession number on Orthanc
# Uses DCMTK findscu (verified working)
# ---------------------------------------------------------------------------


def find_study_by_accession(cfg: Config, accession_number: str) -> str | None:
    """
    Query Orthanc via C-FIND for the given accession number.

    Returns the StudyInstanceUID, or None if not found.
    Uses DCMTK findscu.
    """
    logger.debug(
        "C-FIND %s on %s:%d (AET: %s)",
        accession_number,
        cfg.orthanc_host,
        cfg.orthanc_port,
        cfg.orthanc_aet,
    )

    try:
        result = subprocess.run(
            [
                "findscu",
                "-aet", cfg.worker_aet,
                "-aec", cfg.orthanc_aet,
                "-k", "QueryRetrieveLevel=STUDY",
                "-k", f"AccessionNumber={accession_number}",
                "-k", "StudyInstanceUID=",
                "-k", "PatientName=",
                "-k", "StudyDescription=",
                "-k", "StudyDate=",
                cfg.orthanc_host,
                str(cfg.orthanc_port),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        logger.error("C-FIND timed out for %s", accession_number)
        return None
    except FileNotFoundError:
        logger.error("DCMTK findscu not found. Install dcmtk package.")
        return None

    output = result.stdout + result.stderr
    if logger.isEnabledFor(logging.DEBUG):
        # Show last 10 lines only (first lines are verbose)
        lines = output.strip().split("\n")
        last = "\n".join(lines[-10:]) if len(lines) > 10 else output
        logger.debug("findscu output for %s:\n%s", accession_number, last)

    # Parse the output for StudyInstanceUID
    # DCMTK format: (0020,000d) UI [1.2.3.4.5...] # ... StudyInstanceUID
    uid_match = re.search(r'\(0020,000d\)\s+UI\s+\[([^\]]+)\]', output)
    if uid_match:
        uid = uid_match.group(1).strip()
        if uid:
            logger.info("Found study %s for accession %s", uid, accession_number)
            return uid

    # Also check if any results were returned at all
    if "RSP: pending" in output or "StudyInstanceUID" in output:
        logger.info("C-FIND found no study for accession %s (query completed, no matches)", accession_number)
    else:
        logger.warning("C-FIND for %s returned no recognizable data", accession_number)

    return None


# ---------------------------------------------------------------------------
# XNAT Prearchive Builder
# Builds sessions from prearchive data after successful C-MOVE
# ---------------------------------------------------------------------------


def _xnat_login(cfg: Config) -> str | None:
    """Login to XNAT and return a JSESSIONID token."""
    try:
        req = urllib.request.Request(
            f"{cfg.xnat_url}/data/JSESSION",
            method="POST",
            data=b"",
        )
        credentials = f"{cfg.xnat_admin_user}:{cfg.xnat_admin_password}"
        encoded = base64.b64encode(credentials.encode()).decode()
        req.add_header("Authorization", f"Basic {encoded}")
        with urllib.request.urlopen(req, timeout=10) as resp:
            token = resp.read().decode().strip()
            logger.info("Authenticated with XNAT as %s", cfg.xnat_admin_user)
            return token
    except Exception:
        logger.error("XNAT login failed — see debug logs for details")
        logger.debug("XNAT login failure details:", exc_info=True)
        return None


def _xnat_rest(cfg: Config, token: str, method: str, path: str,
               data: str | None = None,
               content_type: str = "application/xml") -> tuple[int, str]:
    """Make a REST call to XNAT. Returns (status_code, response_body)."""
    try:
        req = urllib.request.Request(
            f"{cfg.xnat_url}{path}",
            method=method,
            data=data.encode() if data else None,
            headers={
                "Cookie": f"JSESSIONID={token}",
                "Content-Type": content_type,
            } if data else {
                "Cookie": f"JSESSIONID={token}",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def _ensure_subject(cfg: Config, token: str, project_id: str,
                    patient_id: str, patient_name: str) -> str | None:
    """
    Ensure a subject exists in the project.
    Returns the XNAT internal subject ID (e.g. XNAT_S00001) or None on failure.
    """
    # First check if subject already exists
    status, body = _xnat_rest(cfg, token, "GET",
        f"/data/projects/{project_id}/subjects?format=json",
        content_type="application/json")
    if status == 200:
        import json
        data = json.loads(body)
        results = data.get("ResultSet", {}).get("Result", [])
        for r in results:
            if r.get("label") == patient_id:
                logger.info("Subject %s already exists (ID: %s)", patient_id, r["ID"])
                return r["ID"]

    # Create subject using XML
    xml = f'''<?xml version='1.0' encoding='UTF-8'?>
<xnat:subjectData xmlns:xnat='http://nrg.wustl.edu/xnat'
    xmlns:xsi='http://www.w3.org/2001/XMLSchema-instance'
    xsi:type='xnat:subjectData'>
  <xnat:label>{patient_id}</xnat:label>
  <xnat:project>{project_id}</xnat:project>
  <xnat:sharing>
    <xnat:share>
      <xnat:project>{project_id}</xnat:project>
    </xnat:share>
  </xnat:sharing>
</xnat:subjectData>'''

    req = urllib.request.Request(
        f"{cfg.xnat_url}/data/archive/projects/{project_id}/subjects/{patient_id}",
        method="PUT",
        data=xml.encode(),
        headers={
            "Cookie": f"JSESSIONID={token}",
            "Content-Type": "application/xml",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            subject_id = resp.read().decode().strip()
            logger.info("Created subject %s (ID: %s)", patient_id, subject_id)
            return subject_id
    except urllib.error.HTTPError as e:
        body = e.read().decode().strip()
        logger.error("Failed to create subject %s: HTTP %d %s", patient_id, e.code, body[:100])
        return None


def _find_prearchive_folder(cfg: Config, token: str, accession_number: str) -> tuple[str, str, str] | None:
    """
    Find the prearchive session matching the given accession number.
    Uses the XNAT prearchive REST API to find the session.

    Returns (timestamp, folder_name, subject_name) or None.
    """
    try:
        req = urllib.request.Request(
            f"{cfg.xnat_url}/data/prearchive?format=json",
            headers={"Cookie": f"JSESSIONID={token}"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:
        logger.error("Failed to query prearchive API: %s", e)
        return None

    results = data.get("ResultSet", {}).get("Result", [])
    # Reverse sort by timestamp descending (newest first)
    results.sort(key=lambda x: x.get("timestamp", ""), reverse=True)

    # The C-MOVE just happened, the newest session should be ours
    # We match by tag (StudyInstanceUID) if available, or assume newest
    for r in results[:3]:
        ts = r.get("timestamp", "")
        folder = r.get("folderName", "")
        subject = r.get("subject", "")
        if ts and folder:
            logger.info(
                "Found prearchive session %s/%s (subject=%s)",
                ts, folder, subject or "N/A"
            )
            return (ts, folder, subject or "Patient")

    return None


def _generate_session_xml(project_id: str, patient_id: str, patient_name: str,
                          accession_number: str, modality: str = "CT") -> str:
    """Generate a prearchive session XML for the build API."""
    import datetime
    today = datetime.date.today().isoformat()
    return f'''<?xml version='1.0' encoding='UTF-8'?>
<xnat:ctSessionData xmlns:xnat='http://nrg.wustl.edu/xnat' xmlns:xsi='http://www.w3.org/2001/XMLSchema-instance'
    ID="{patient_id}" project="{project_id}" label="{patient_id}"
    xsi:type='xnat:ctSessionData'>
  <xnat:date>{today}</xnat:date>
  <xnat:time>12:00:00</xnat:time>
  <xnat:modality>{modality}</xnat:modality>
  <xnat:subject_ID>{patient_id}</xnat:subject_ID>
  <xnat:dcmAccessionNumber>{accession_number}</xnat:dcmAccessionNumber>
  <xnat:project>{project_id}</xnat:project>
</xnat:ctSessionData>'''


def _find_prearchive_session(cfg: Config, token: str, accession_number: str) -> tuple[str, str] | None:
    """
    Find the prearchive session XML matching the given accession number.

    Uses the XNAT prearchive API. Returns (prearchive_path, xml_content)
    or None. If no XML file exists, generates one from session metadata.
    """
    result = _find_prearchive_folder(cfg, token, accession_number)
    if result is None:
        return None

    ts, folder_name, subject = result
    src = f"/prearchive/projects/Unassigned/{ts}/{folder_name}"

    # Generate session XML
    xml = f'''<?xml version='1.0' encoding='UTF-8'?>
<xnat:ctSessionData xmlns:xnat='http://nrg.wustl.edu/xnat'
    xmlns:xsi='http://www.w3.org/2001/XMLSchema-instance'
    xsi:type='xnat:ctSessionData'>
  <xnat:subject_ID>{subject}</xnat:subject_ID>
  <xnat:label>{accession_number}</xnat:label>
  <xnat:project>PROJECT_PLACEHOLDER</xnat:project>
</xnat:ctSessionData>'''

    return (src, xml)


def _extract_session_label(xml_content: str) -> str | None:
    """Extract the session label from prearchive XML."""
    try:
        root = ET.fromstring(xml_content)
        label = root.get("label") or root.get("ID")
        return label
    except Exception:
        return None


def build_prearchive_session(cfg: Config, project_id: str, accession_number: str) -> bool:
    """
    Build an XNAT session from prearchive data after DICOM import.

    Steps:
      1. Wait for prearchive data to be written
      2. Find the prearchive folder (XML or DICOM files)
      3. Generate session XML if DQR plugin didn't create one
      4. Ensure subject exists in project
      5. Call XNAT REST API to build/archive the session

    This handles the case where the DQR plugin doesn't create a session XML
    (due to PacsNotStorableException bug), by generating one manually.

    Returns True if successful.
    """
    logger.info(
        "Building prearchive session for accession %s in project %s",
        accession_number,
        project_id,
    )

    # Step 1: Login and wait for prearchive data via the API
    token = _xnat_login(cfg)
    if not token:
        return False

    for attempt in range(cfg.prearchive_poll_secs):
        result = _find_prearchive_session(cfg, token, accession_number)
        if result is not None:
            src, xml_template = result
            # Replace project placeholder
            xml_content = xml_template.replace("PROJECT_PLACEHOLDER", project_id)
            logger.info("Found prearchive session for %s at %s", accession_number, src)
            break
        logger.debug("Waiting for prearchive data (attempt %d/%d)", attempt + 1, cfg.prearchive_poll_secs)
        time.sleep(1)
    else:
        logger.warning("Prearchive session for accession %s not found after %d seconds",
                       accession_number, cfg.prearchive_poll_secs)
        return False

    # Step 2: Extract session label from XML
    session_label = _extract_session_label(xml_content)
    if not session_label:
        session_label = accession_number

    # Step 3: Ensure subject exists
    subject_name = _extract_subject_name(xml_content) or "Patient"
    subject_id = _ensure_subject(cfg, token, project_id, subject_name, subject_name)
    if not subject_id:
        logger.error("Could not create subject for project %s", project_id)
        return False

    # Step 4: Build session via XNAT REST API using the src path
    import re
    updated_xml = re.sub(
        r'<xnat:project>[^<]+</xnat:project>',
        f'<xnat:project>{project_id}</xnat:project>',
        xml_content
    )
    updated_xml = re.sub(
        r'<xnat:label>[^<]+</xnat:label>',
        f'<xnat:label>{accession_number}</xnat:label>',
        updated_xml
    )

    import urllib.parse as urlparse
    build_url = (
        f"{cfg.xnat_url}/data/archive/projects/{project_id}/experiments/{accession_number}"
        f"?src={urlparse.quote(src)}"
        f"&override=true"
        f"&autoArchive=true"
        f"&inbody=true"
    )

    try:
        req = urllib.request.Request(
            build_url,
            method="PUT",
            data=updated_xml.encode(),
            headers={
                "Content-Type": "application/xml",
                "Cookie": f"JSESSIONID={token}",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode().strip()
            logger.info(
                "Session built: %s (HTTP %d, body: %s)",
                accession_number, resp.status, body[:100],
            )
            return True
    except urllib.error.HTTPError as e:
        body = e.read().decode().strip()
        logger.warning(
            "Build API returned HTTP %d for project %s: %.100s",
            e.code, project_id, body,
        )
        # Return False so the caller (process_batch) tries the fallback
        return False
    except Exception as e:
        logger.error("Error building session: %s", e)
        return False


def _extract_subject_name(xml_content: str) -> str | None:
    """Extract the subject name from session XML."""
    import re
    m = re.search(r'<xnat:subject_ID>([^<]+)</xnat:subject_ID>', xml_content)
    return m.group(1) if m else None


def _extract_session_label(xml_content: str) -> str | None:
    """Extract the session label from session XML."""
    import re
    m = re.search(r'<xnat:label>([^<]+)</xnat:label>', xml_content)
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# DICOM C-MOVE - transfer study from Orthanc to XNAT
# Uses DCMTK movescu (verified working, unlike pynetdicom)
# ---------------------------------------------------------------------------


def move_study(cfg: Config, study_uid: str, accession_number: str) -> bool:
    """
    C-MOVE a study from Orthanc to XNAT using DCMTK movescu.

    Uses `-P --port` to listen for C-STORE sub-operations from Orthanc.
    Without this, movescu sends the C-MOVE request but doesn't receive
    the DICOM data that Orthanc pushes back.

    Destination AE (XNAT) must be configured in Orthanc's DicomModalities.
    """
    logger.info(
        "C-MOVE study %s (accession %s) from %s to %s",
        study_uid,
        accession_number,
        cfg.orthanc_aet,
        cfg.xnat_aet,
    )

    # Pick a random ephemeral port for movescu to listen on
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(('', 0))
    listen_port = s.getsockname()[1]
    s.close()

    try:
        result = subprocess.run(
            [
                "movescu",
                "-v",
                "-aec", cfg.orthanc_aet,
                "-aet", cfg.worker_aet,
                "-aem", cfg.xnat_aet,
                "-P",
                "--port", str(listen_port),
                "-k", "0008,0052=STUDY",
                "-k", f"0020,000D={study_uid}",
                cfg.orthanc_host,
                str(cfg.orthanc_port),
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        logger.error("C-MOVE timed out for %s (study %s)", accession_number, study_uid)
        return False
    except FileNotFoundError:
        logger.error("DCMTK movescu not found. Install dcmtk package.")
        return False

    output = result.stdout + result.stderr
    if logger.isEnabledFor(logging.DEBUG):
        lines = output.strip().split("\n")
        last = "\n".join(lines[-5:]) if len(lines) > 5 else output
        logger.debug("movescu output for %s:\n%s", accession_number, last)

    if result.returncode == 0:
        # Check for success indicators in the output (movescu sends to stderr)
        combined = output
        if "0x0000: Success" in combined:
            logger.info("C-MOVE successful for %s (study %s)", accession_number, study_uid)
            return True
        elif "No such object" in combined or "Failed" in combined:
            logger.error("C-MOVE failed for %s: study not found on Orthanc", accession_number)
            return False
        else:
            # Exit code 0 with no error messages = success
            logger.info("C-MOVE completed for %s (study %s)", accession_number, study_uid)
            return True
    else:
        logger.error(
            "C-MOVE failed for %s (exit code %d): %.300s",
            accession_number,
            result.returncode,
            output or "(no output)",
        )
        return False


# ---------------------------------------------------------------------------
# Main worker loop
# ---------------------------------------------------------------------------


def process_batch(cfg: Config) -> int:
    """Process one batch of queued requests. Returns number processed."""
    logger.info("Connecting to XNAT DB at %s:%d...", cfg.xnat_db_host, cfg.xnat_db_port)
    conn = get_db_connection(cfg)

    try:
        requests = fetch_queued_requests(conn, cfg)
        if not requests:
            logger.info("No QUEUED requests found. Idle.")
            return 0

        logger.info("Found %d QUEUED request(s) to process.", len(requests))

        for req in requests:
            req_id = req["id"]
            acc_no = req["accession_number"] or ""
            study_uid = req.get("study_instance_uid") or ""

            if not acc_no:
                logger.warning("Request %d has no accession number, skipping.", req_id)
                mark_request_failed(conn, req_id, "No accession number")
                continue

            logger.info("--- Processing request %d: accession %s ---", req_id, acc_no)

            # Step 1: C-FIND to get StudyInstanceUID if not already known
            if not study_uid:
                study_uid = find_study_by_accession(cfg, acc_no)
                if not study_uid:
                    logger.warning("No study found for accession %s (request %d)", acc_no, req_id)
                    mark_request_failed(conn, req_id, f"No study found for accession {acc_no}")
                    continue
            else:
                logger.info("Using known StudyInstanceUID: %s", study_uid)

            # Step 2: C-MOVE to XNAT
            success = move_study(cfg, study_uid, acc_no)
            if success:
                # Clean up DQR plugin failures that show as "Processing" in hub
                cleanup_dqr_failures(conn, acc_no)

                # Step 3: Build session from prearchive
                xnat_project_id = req.get("xnat_project") or ""
                if xnat_project_id:
                    built = build_prearchive_session(cfg, xnat_project_id, acc_no)
                    if not built:
                        logger.warning(
                            "Session build failed for accession %s in project %s, "
                            "trying fallback build in working project...",
                            acc_no,
                            xnat_project_id,
                        )
                        # Fallback: build in a working project, then move via DB
                        working_project = _get_working_project(conn)
                        if working_project and working_project != xnat_project_id:
                            fallback_built = build_prearchive_session(cfg, working_project, acc_no)
                            if fallback_built:
                                # Build the label the same way build_prearchive_session does
                                # Use accession number as label
                                moved = move_experiment_via_db(conn, working_project, xnat_project_id, acc_no)
                                if moved:
                                    logger.info(
                                        "Fallback build successful: experiment %s moved to %s",
                                        acc_no,
                                        xnat_project_id,
                                    )
                                    built = True
                                else:
                                    logger.error(
                                        "Fallback build worked but move failed for %s",
                                        acc_no,
                                    )
                            else:
                                logger.warning(
                                    "Fallback build also failed for %s in project %s",
                                    acc_no,
                                    working_project,
                                )
                        else:
                            logger.warning(
                                "No working project found for fallback build of %s",
                                acc_no,
                            )
                else:
                    logger.warning("No XNAT project ID in queue entry %d, skipping prearchive build", req_id)

                mark_request_completed(conn, req_id, study_uid)
            else:
                mark_request_failed(conn, req_id, "C-MOVE failed")

            # Small delay between moves to avoid overwhelming XNAT
            time.sleep(0.5)

        logger.info("Batch complete: processed %d request(s).", len(requests))
        return len(requests)

    except psycopg2.OperationalError as e:
        logger.error("Database connection error: %s", e)
        return -1
    except Exception as e:
        logger.error("Unexpected error: %s", e)
        return -1
    finally:
        conn.close()


def main():
    """Entry point."""
    cfg = Config()

    # Set up logging
    log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        stream=sys.stdout,
    )
    logger.setLevel(getattr(logging, log_level, logging.INFO))

    logger.info("Imaging Import Worker starting (RUN_ONCE=%s)", cfg.run_once)
    logger.info(
        "PACS: %s@%s:%d %s XNAT: %s@%s:%d",
        cfg.orthanc_aet,
        cfg.orthanc_host,
        cfg.orthanc_port,
        "\u2192",
        cfg.xnat_aet,
        cfg.xnat_dicom_host,
        cfg.xnat_dicom_port,
    )

    if cfg.run_once:
        process_batch(cfg)
    else:
        while True:
            try:
                processed = process_batch(cfg)
                logger.info("Sleeping %d seconds before next poll...", cfg.poll_interval)
                time.sleep(cfg.poll_interval)
            except KeyboardInterrupt:
                logger.info("Shutting down.")
                break
            except Exception as e:
                logger.error("Poll cycle failed: %s", e)
                time.sleep(60)


if __name__ == "__main__":
    main()
