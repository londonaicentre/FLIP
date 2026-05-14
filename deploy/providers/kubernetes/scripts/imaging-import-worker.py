#!/usr/bin/env python3
"""
FLIP Imaging Import Worker (DQR Bypass)

Bypasses the XNAT DQR plugin (which throws PacsNotStorableException on the
K8s trust) by directly performing C-MOVE operations from Orthanc (PACS) to
XNAT over DICOM.

Designed to run as a Kubernetes Job (or one-off on the dcmtk pod).

Workflow:
  1. Connect to the xnat-db via service DNS (host=xnat-db, port=5432).
  2. Read all QUEUED entries from xhbm_queued_pacs_request.
  3. For each:
     a. Use pynetdicom to C-FIND Orthanc for the accession number
     b. Use pynetdicom to C-MOVE the study from Orthanc to XNAT
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
"""

import logging
import os
import sys
import time
from dataclasses import dataclass

import psycopg2
import psycopg2.extras
from pydicom.dataset import Dataset
from pydicom.uid import ExplicitVRLittleEndian
from pynetdicom import AE, QueryRetrievePresentationContexts
from pynetdicom.sop_class import (
    PatientRootQueryRetrieveInformationModelFind,
    PatientRootQueryRetrieveInformationModelMove,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

logger = logging.getLogger("imaging-import-worker")


@dataclass
class Config:
    xnat_db_host: str = os.environ.get("XNAT_DB_HOST", "xnat-db")
    xnat_db_port: int = int(os.environ.get("XNAT_DB_PORT", "5432"))
    xnat_db_user: str = os.environ.get("XNAT_DB_USER", "xnat")
    xnat_db_password: str = os.environ.get("XNAT_DB_PASSWORD", "")
    xnat_db_name: str = os.environ.get("XNAT_DB_NAME", "xnat")

    orthanc_host: str = os.environ.get("ORTHANC_HOST", "orthanc")
    orthanc_port: int = int(os.environ.get("ORTHANC_PORT", "4242"))
    orthanc_aet: str = os.environ.get("ORTHANC_AET", "ORTHANC")

    xnat_aet: str = os.environ.get("XNAT_AET", "XNAT")
    xnat_dicom_host: str = os.environ.get("XNAT_DICOM_HOST", "xnat-web")
    xnat_dicom_port: int = int(os.environ.get("XNAT_DICOM_PORT", "8104"))

    worker_aet: str = os.environ.get("WORKER_AET", "FLIPIMPORT")

    batch_size: int = int(os.environ.get("BATCH_SIZE", "50"))
    poll_interval: int = int(os.environ.get("POLL_INTERVAL", "300"))
    run_once: bool = os.environ.get("RUN_ONCE", "true").lower() == "true"

    pacs_id: int = int(os.environ.get("PACS_ID", "1"))


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
                 AND disabled IS NULL
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
        # Copy the row from queued to executed
        cur.execute(
            """INSERT INTO xhbm_executed_pacs_request
               (created, accession_number, status, xnat_project, timestamp,
                disabled, enabled, destination_ae_title, error_message,
                pacs_id, patient_id, patient_name, priority, queued_time,
                remapping_script, request_id, study_date, study_id,
                study_instance_uid, username, executed_time)
               SELECT created, accession_number, 'COMPLETED', xnat_project, NOW(),
                      'infinity', false, destination_ae_title, NULL,
                      pacs_id, patient_id, patient_name, priority, queued_time,
                      remapping_script, request_id, study_date, study_id,
                      %s, username, NOW()
               FROM xhbm_queued_pacs_request
               WHERE id = %s
            """,
            (study_uid or "", request_id),
        )
        # Delete from queued
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


# ---------------------------------------------------------------------------
# DICOM C-FIND — find study by accession number on Orthanc
# ---------------------------------------------------------------------------


def find_study_by_accession(cfg: Config, accession_number: str) -> str | None:
    """
    Query Orthanc via C-FIND for the given accession number.

    Returns the StudyInstanceUID, or None if not found.
    """
    ae = AE(ae_title=cfg.worker_aet)
    ae.requested_contexts = QueryRetrievePresentationContexts

    # Build the C-FIND request
    ds = Dataset()
    ds.QueryRetrieveLevel = "STUDY"
    ds.AccessionNumber = accession_number
    ds.StudyInstanceUID = ""  # We want this in the response

    logger.debug("C-FIND %s on %s:%d (AET: %s)", accession_number, cfg.orthanc_host, cfg.orthanc_port, cfg.orthanc_aet)

    assoc = ae.associate(cfg.orthanc_host, cfg.orthanc_port, ae_title=cfg.orthanc_aet)
    if not assoc.is_established:
        logger.error("C-FIND association failed for %s: could not connect to Orthanc", accession_number)
        return None

    try:
        responses = assoc.send_c_find(ds, PatientRootQueryRetrieveInformationModelFind)
        for status, result_ds in responses:
            if status and status.Status in (0xFF00, 0xFF01):
                if result_ds and "StudyInstanceUID" in result_ds:
                    uid = result_ds.StudyInstanceUID
                    logger.info(
                        "Found study %s for accession %s (patient: %s)",
                        uid,
                        accession_number,
                        getattr(result_ds, "PatientName", "N/A"),
                    )
                    return uid
        # No results or only PENDING
        logger.info("C-FIND found no study for accession %s", accession_number)
        return None
    except Exception as e:
        logger.error("C-FIND error for %s: %s", accession_number, e)
        return None
    finally:
        assoc.release()


# ---------------------------------------------------------------------------
# DICOM C-MOVE — transfer study from Orthanc to XNAT
# ---------------------------------------------------------------------------


def move_study(cfg: Config, study_uid: str, accession_number: str) -> bool:
    """
    C-MOVE a study from Orthanc to XNAT.

    Args:
        cfg: Configuration
        study_uid: The StudyInstanceUID to move
        accession_number: For logging

    Returns:
        True if the study was successfully C-MOVEd
    """
    ae = AE(ae_title=cfg.worker_aet)
    ae.requested_contexts = QueryRetrievePresentationContexts

    ds = Dataset()
    ds.QueryRetrieveLevel = "STUDY"
    ds.StudyInstanceUID = study_uid

    logger.info(
        "C-MOVE study %s (accession %s) from %s to %s (AET: %s) on %s:%d",
        study_uid,
        accession_number,
        cfg.orthanc_aet,
        cfg.xnat_aet,
        cfg.xnat_aet,
        cfg.orthanc_host,
        cfg.orthanc_port,
    )

    assoc = ae.associate(cfg.orthanc_host, cfg.orthanc_port, ae_title=cfg.orthanc_aet)
    if not assoc.is_established:
        logger.error("C-MOVE association failed for %s: could not connect to Orthanc", accession_number)
        return False

    try:
        responses = assoc.send_c_move(
            ds,
            cfg.xnat_aet,
            PatientRootQueryRetrieveInformationModelMove,
        )
        for status, result_ds in responses:
            if status:
                status_code = status.Status
                if status_code == 0x0000:  # Success
                    logger.info("C-MOVE successful for %s (study %s)", accession_number, study_uid)
                    return True
                elif status_code == 0xFF00:  # Pending — still transferring
                    remaining = getattr(status, "NumberOfRemainingSubOperations", "?")
                    completed = getattr(status, "NumberOfCompletedSubOperations", "?")
                    logger.debug(
                        "C-MOVE pending for %s: %s remaining, %s completed",
                        accession_number,
                        remaining,
                        completed,
                    )
                elif status_code == 0xB000:  # Warning
                    logger.warning("C-MOVE warning for %s (0x%04X)", accession_number, status_code)
                    return True
                else:
                    logger.error(
                        "C-MOVE failed for %s: status 0x%04X (%s)",
                        accession_number,
                        status_code,
                        status.ErrorComment if hasattr(status, "ErrorComment") else "unknown",
                    )
                    return False
            else:
                # No status at all
                logger.error("C-MOVE returned no status for %s", accession_number)
                return False
        # All responses were pending and the loop ended
        logger.warning("C-MOVE for %s ended without final status — assuming complete", accession_number)
        return True
    except Exception as e:
        logger.error("C-MOVE exception for %s: %s", accession_number, e)
        return False
    finally:
        assoc.release()


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
                mark_request_completed(conn, req_id, study_uid)
            else:
                mark_request_failed(conn, req_id, "C-MOVE failed")

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
        "PACS: %s@%s:%d → XNAT: %s@%s:%d",
        cfg.orthanc_aet,
        cfg.orthanc_host,
        cfg.orthanc_port,
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
