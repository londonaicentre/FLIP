# Copyright (c) Guy's and St Thomas' NHS Foundation Trust & King's College London
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

import datetime
import uuid
from typing import Any

import pandas as pd
from cryptography.exceptions import InvalidTag
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import SQLAlchemyError

from data_access_api.config import get_policy, get_settings
from data_access_api.policy import (
    ACTION_COHORT_ACCESSION_IDS,
    ACTION_COHORT_DATAFRAME,
    ACTION_COHORT_STATISTICS,
    Decision,
    decide,
    subject_attributes,
)
from data_access_api.routers.schema import (
    AccessionIdsResponse,
    CohortQueryInput,
    DataframeQuery,
    SnapshotCreateRequest,
    SnapshotDeleteRequest,
    SnapshotResponse,
    StatisticsResponse,
)
from data_access_api.services.cohort import (
    ACCESSION_ID_COLUMN,
    SUBJECT_ID_COLUMN,
    count_distinct_subjects,
    get_records,
    get_statistics,
    keep_imaging_accessions,
    validate_query,
)
from data_access_api.services.cohort_snapshot import (
    Snapshot,
    SnapshotExists,
    SnapshotTooLarge,
    SnapshotUnreadable,
    delete_snapshot,
    get_snapshot,
    load_snapshot,
    normalised_query_hash,
    save_snapshot,
    snapshot_enabled,
)
from data_access_api.utils.encryption import PROJECT_ID_CONTEXT, decrypt
from data_access_api.utils.internal_auth import authenticate_cohort_admin, authenticate_internal_service
from data_access_api.utils.logger import logger

# Returned instead of row-level data when a cohort is smaller than
# COHORT_QUERY_THRESHOLD, by both row-level routes (/cohort/dataframe and
# /cohort/accession-ids). Deliberately fixed text: it must be identical for a
# cohort of zero and a cohort of threshold-minus-one, or the refusal itself
# becomes a one-row oracle for probing the database.
_BELOW_THRESHOLD_DETAIL = "Cohort is too small for row-level data to be released."

# Returned by the row-level routes for a project with no frozen cohort membership.
# Row-level data is released ONLY for the membership recorded at approval
# (FLIP#857); there is no path that serves a caller's own SQL. Deliberately
# generic: it must not reveal whether the project exists.
_NO_SNAPSHOT_DETAIL = "No approved cohort snapshot exists for this project."

# Returned when a cohort's subject count cannot be established from its own projection. This
# reports the SHAPE of the query and never anything about the data behind it, so unlike
# _BELOW_THRESHOLD_DETAIL it is safe to be specific: it cannot be used as a membership oracle,
# and a researcher needs to know exactly which column to add.
_UNCOUNTABLE_SUBJECTS_DETAIL = (
    "Cohort query must expose the subjects it covers so the disclosure threshold can be applied "
    "per subject: add a person_id column to the SELECT list, or an accession_id column for an "
    "imaging cohort."
)


def _evaluate(action: str, project_id: str | None) -> Decision:
    """Ask the trust's governance policy about one cohort operation (FLIP#1259).

    The single decision point for this router. Returns the decision rather than raising,
    because each route owns its own refusal text and status code — and those must not
    change. In particular the caller must keep answering ``_BELOW_THRESHOLD_DETAIL``:
    naming the rule in the HTTP body would turn the refusal into a probe for the trust's
    configuration, and on the row-level routes into a row-count oracle.

    ``/cohort`` calls ``validate_query`` before this, so the query's shape is judged the same
    whatever the policy says; deciding first made an invalid query a 400 when permitted and a
    403 when denied. The two row-level routes ignore the caller's SQL (they run the stored
    query of record, FLIP#857) and decide before reading the frozen membership, so a denied
    project's query never runs and a denial answers the same whether or not a membership exists.
    The one difference left is time: a denial answers without the query's round trip. That
    tells a caller holding the trust-internal key whether its own project is denied, which its
    training job learns anyway when every fetch is refused.
    """
    return decide(
        subject_attributes(),
        {"project_id": project_id},
        action,
        policy=get_policy(),
        configured_threshold=get_settings().COHORT_QUERY_THRESHOLD,
    )


def _log_denial(decision: Decision, project_id: str | None, what: str) -> None:
    """Record an attributable denial (FLIP#1259 AC 6).

    ``rule_id`` goes to the log, never to the caller. This is the only place a denial
    becomes traceable to the rule that caused it, so it carries the project too.
    """
    logger.warning(
        f"Policy denied {what} for project {project_id or '<none>'}: "
        f"rule_id={decision.rule_id} reason={decision.reason}"
    )


def _open_project_id(encrypted_project_id: str) -> str:
    """Open the hub-sealed project id the FL client forwards, or answer 400 with the reason.

    The id is authenticated under the ``project_id`` context. A failure is the caller's payload
    (tampered, sealed for another purpose, or the hub's ``AES_KEY_BASE64`` is not this trust's),
    so it is a 400 that names the cause rather than a bare 500. Anything else is a fault on this
    side (the key cannot be loaded, the cipher itself) and is a logged 500 that names the type —
    the same taxonomy as imaging-api's routers, which open the same envelope.
    """
    try:
        return decrypt(encrypted_project_id, context=PROJECT_ID_CONTEXT)
    except InvalidTag:
        logger.error("encrypted_project_id failed authentication")
        raise HTTPException(status_code=400, detail="encrypted_project_id failed authentication")
    except (ValueError, KeyError) as e:
        logger.error(f"encrypted_project_id is not a valid envelope: {e}")
        raise HTTPException(status_code=400, detail=f"encrypted_project_id is not a valid envelope: {e}")
    except Exception as e:
        # Not the caller's payload. Name the type so an empty exception message never yields a blank reason.
        logger.exception(f"Failed to decrypt encrypted_project_id ({type(e).__name__}): {e}")
        raise HTTPException(status_code=500, detail=f"Failed to decrypt encrypted_project_id ({type(e).__name__})")


def _project_id_for_policy(encrypted_project_id: str) -> str | None:
    """Open the project id for a policy decision on ``/cohort``, or return ``None``.

    ``/cohort`` does not otherwise need the project id, so opening the envelope here would
    introduce a 400 this route has never returned — a visible behaviour change for trusts
    running no policy at all. It is therefore opened only when a rule could actually use
    it: some rule names projects for this action.

    A policy that cannot be applied must not fail open, so a project-scoped rule with an
    unopenable envelope still raises the 400 from ``_open_project_id``. When no rule is
    project-scoped, ``None`` is correct rather than merely convenient: no rule can match on
    a project, so the decision does not depend on the value.
    """
    policy = get_policy()
    if policy is None or not policy.scopes_projects(ACTION_COHORT_STATISTICS):
        return None
    return _open_project_id(encrypted_project_id)


# Two routers under the same /cohort prefix, split by privilege (FLIP#857).
#
# read_router — statistics + the two row-level serve routes. Gated on the trust-internal key
# alone: every trust-internal service (trust-api, imaging-api, fl-client) legitimately reads
# here, and the routes serve only the approved, project-scoped, threshold-gated cohort.
#
# write_router — the routes that DEFINE/destroy the frozen membership the row-level routes then
# serve. Gated on the trust-internal key AND cohort-admin (proof of possessing AES_KEY_BASE64),
# so a caller must be both a trust-internal service and one of the two trusted to define cohorts.
# fl-client holds the trust-internal key but no AES key, so it can read its cohort but never
# rewrite or delete it. The dependency order matters: trust-internal runs first, so a caller
# without it gets 401 before the cohort-admin check ever runs (which returns 403).
read_router = APIRouter(prefix="/cohort", tags=["Cohort"], dependencies=[Depends(authenticate_internal_service)])
write_router = APIRouter(
    prefix="/cohort",
    tags=["Cohort"],
    dependencies=[Depends(authenticate_internal_service), Depends(authenticate_cohort_admin)],
)


def _require_snapshot(project_id: str) -> Snapshot:
    """The project's frozen cohort membership, or the fixed 403 when there is none.

    The membership recorded at approval is the ONLY basis for row-level data: a project that
    was never approved (or whose record was purged, or whose trust has the store
    unconfigured/unwritable) is refused. Fail-closed by construction — there is no path that
    serves a caller's own SQL.
    """
    snapshot = get_snapshot(project_id)
    if snapshot is None:
        logger.warning(f"Refusing row-level data for project {project_id}: no approved cohort snapshot")
        raise HTTPException(status_code=403, detail=_NO_SNAPSHOT_DETAIL)
    return snapshot


def _check_threshold(project_id: str, subject_count: int, minimum_cohort_size: int) -> None:
    """Apply the disclosure threshold to a distinct-subject count.

    The refusal reuses the fixed below-threshold text so an empty cohort and a below-threshold
    one stay indistinguishable. The threshold is the governance decision's
    ``effective_threshold``, taken live, so an operator RAISING their disclosure floor — in the
    kit or in a governance rule — takes effect on already-approved projects.
    """
    if subject_count < minimum_cohort_size:
        logger.warning(
            f"Withholding cohort for project {project_id}: "
            f"it covers fewer than the minimum {minimum_cohort_size} subjects"
        )
        raise HTTPException(status_code=403, detail=_BELOW_THRESHOLD_DETAIL)


def _log_ignored_client_query(project_id: str, snapshot: Snapshot, client_query: str) -> None:
    """Record that the caller-supplied SQL was ignored in favour of the query of record.

    Running only the stored query is what closes the arbitrary-SQL exposure on these routes
    (see FLIP#857's audit note): the only row-level data obtainable under a project's id is the
    cohort that was approved. The hash comparison exists purely so a mismatch is visible in the
    trust's logs; the ``query`` field stays in the request schema because the FL client library
    sends it.
    """
    if normalised_query_hash(client_query) != snapshot.query_hash:
        logger.warning(
            f"Client-supplied query for project {project_id} differs from the approved cohort "
            "query — ignored; serving the query of record."
        )


def _id_str(value: Any) -> str:
    """One canonical string per id, whatever dtype the column arrived as.

    A nullable integer column comes back as float64 when it holds a NULL, so ``7`` and ``7.0``
    must compare equal between the approval-time run and a later one. (float64 is exact only up to
    2**53; an id beyond that which reached pandas as a float has already lost precision and will
    not match — it drops out, which is the fail-closed direction.)
    """
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _frozen_ids(df: pd.DataFrame, column: str) -> list[str] | None:
    """The distinct non-null values of ``column`` as canonical strings, or None when absent."""
    if column not in df.columns:
        return None
    return sorted({_id_str(value) for value in df[column].dropna()})


def _restrict_to_membership(df: pd.DataFrame, snapshot: Snapshot) -> pd.DataFrame:
    """Keep only the rows whose ids are in the frozen membership.

    A row is kept when every frozen column it carries a value in holds a member, and at least one
    does. So neither a patient who joined OMOP after approval nor a new study of an approved patient
    enters the cohort, while a row with no value in one frozen column (a member's row whose
    LEFT-JOINed ``accession_id`` is NULL) is served as it was counted at approval. A row with no
    value in any frozen column cannot be matched and is dropped. A frozen column the query no longer
    projects matches nothing — fail-closed, since membership can no longer be checked — and so does
    a record freezing no column at all, which ``Snapshot`` already refuses to construct.
    """
    if snapshot.person_ids is None and snapshot.accession_ids is None:
        logger.error("Cohort membership freezes no id column; releasing no rows")
        return df.iloc[0:0]
    consistent = pd.Series(True, index=df.index)
    matched = pd.Series(False, index=df.index)
    for column, ids in ((SUBJECT_ID_COLUMN, snapshot.person_ids), (ACCESSION_ID_COLUMN, snapshot.accession_ids)):
        if ids is None:
            continue
        if column not in df.columns:
            logger.error(f"Query of record no longer projects {column}; releasing no rows")
            return df.iloc[0:0]
        members = set(ids)
        present = df[column].notna()
        member = df[column].map(lambda value: pd.notna(value) and _id_str(value) in members).astype(bool)
        consistent &= ~present | member
        matched |= member
    return df[consistent & matched]


def _run_cohort_query(query: str, *, use_cache: bool, what: str) -> pd.DataFrame:
    """Validate and run a cohort query, with one column per name.

    ``get_records`` already turns driver errors into category-only HTTPExceptions, which are
    re-raised untouched; anything else becomes a category-only 500 — the detail travels back to
    the hub, which shows it to every project member, and raw exception text can carry row values.
    Duplicate column names (``SELECT *`` over a join that keeps both sides' ``person_id``) keep
    their first copy, so every later ``df[column]`` is a Series.
    """
    safe_query = validate_query(query)
    try:
        df = get_records(safe_query, use_cache=use_cache)
    except HTTPException:
        raise
    except SQLAlchemyError:
        logger.exception(f"{what} query failed with a database error")
        raise HTTPException(status_code=500, detail="Query execution failed.")
    except Exception:
        logger.exception(f"{what} query failed unexpectedly")
        raise HTTPException(status_code=500, detail="Query execution failed.")
    return df.loc[:, ~df.columns.duplicated()]


def _count_subjects_or_zero(df: pd.DataFrame, project_id: str) -> int:
    """``count_distinct_subjects`` over live OMOP, with any failure or an uncountable frame counted as zero.

    A count that cannot be taken is refused exactly as a small cohort is, so the refusal never
    says why. Uncached, so a subject removed from OMOP stops counting towards the floor at once.
    """
    try:
        return count_distinct_subjects(df, use_cache=False) or 0
    except Exception:
        logger.exception(f"Subject count unavailable for project {project_id}; refusing as below threshold")
        return 0


@read_router.post("", response_model=StatisticsResponse)
def receive_cohort_query(query_input: CohortQueryInput) -> StatisticsResponse:
    """
    Receives a cohort query and returns the aggregated statistics.

    This is the one route that always evaluates LIVE OMOP: it runs pre-approval by
    definition (it is how a proposed cohort is sized in the first place), and it
    releases aggregates only.

    Below-threshold results are privacy-suppressed: any count below the threshold —
    including a genuine zero — comes back as a normal ``StatisticsResponse`` with
    ``record_count=0``, empty ``data`` and ``suppressed=True``, *not* an HTTP error.
    The threshold counts distinct subjects, and a cohort whose subjects cannot be established
    is suppressed the same way, so the response shape never varies with the cause of a
    shortfall (FLIP#1219 tracks giving the researcher that diagnosis another way). A true
    zero and a small below-threshold count are deliberately indistinguishable so the response
    can't reveal that >=1 patient matched; the ``suppressed`` flag only tells the hub/UI to
    show a "below-threshold" chip rather than a bare 0 (issue #519).

    Args:
        query_input (data_access_api.routers.schema.CohortQueryInput): The input data for the cohort query.

    Returns:
        StatisticsResponse: The aggregated statistics from the query results, or a 0-count response
        when the count is below ``COHORT_QUERY_THRESHOLD``.

    Raises:
        HTTPException: If there is an error during the execution of the query.
    """
    logger.info("Received cohort query")

    # Judged before the policy, so an invalid query is refused the same way whether or not a
    # rule covers this project (see _evaluate).
    safe_query = validate_query(query_input.query)

    # The project id is only opened when a configured policy could actually use it. With no
    # policy this route must behave exactly as before, and decrypting unconditionally would
    # add a 400 failure mode (tampered/foreign envelope) that /cohort never had — a
    # behaviour change for every trust that has not adopted a policy.
    project_id = _project_id_for_policy(query_input.encrypted_project_id)
    decision = _evaluate(ACTION_COHORT_STATISTICS, project_id)
    if not decision.permit:
        _log_denial(decision, project_id, "cohort statistics")
        # Suppressed, not refused. A policy denial must be indistinguishable from a
        # below-threshold cohort on this route, whose contract is that a shortfall comes
        # back as a zero-count response rather than an HTTP error (issue #519) — an error
        # here would tell the caller a policy exists. Shape matches the suppression branch
        # in services.cohort.get_statistics exactly.
        return StatisticsResponse(
            query_id=query_input.query_id,
            trust_id=query_input.trust_id,
            record_count=0,
            created=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d"),
            data=[],
            suppressed=True,
        )

    minimum_cohort_size = decision.effective_threshold
    logger.info(f"Minimum cohort size needed to return statistics: {minimum_cohort_size}")

    # On the original implementation get_records was invoked within get_statistics. However, to better handle
    # exceptions and log the query execution, we separate the two calls here.
    try:
        logger.info("Executing cohort query")

        df = get_records(safe_query)
        if not df.empty:
            # Ignore entirely empty columns. Skipped for a zero-row frame: every column of one is
            # vacuously all-null, so the drop would empty the column list and a cohort that
            # projected person_id would be misdiagnosed as exposing no subject column (FLIP#967
            # produces exactly that frame on a vocabulary-less trust).
            df = df.dropna(axis=1, how="all")
        # drop duplicate columns
        df = df.loc[:, ~df.columns.duplicated()]
    except Exception as e:
        logger.error(f"Error executing query: {str(e)}")
        raise e

    try:
        results = get_statistics(df, query_input=query_input, threshold=minimum_cohort_size)
    except Exception:
        # Detail is category-only: the trust forwards it to the hub, which shows it to every
        # project member, and raw exception text from the aggregation can carry row values.
        logger.exception("Cohort statistics aggregation failed")
        raise HTTPException(status_code=500, detail="Statistics aggregation failed.")

    logger.info("Cohort query returned results")
    return results


@read_router.post("/dataframe")
def get_dataframe(query_input: DataframeQuery) -> dict[str, list[Any]]:
    """
    Serves the project's approved cohort in a DataFrame-like structure (column-oriented dict).

    This is the training-data path: user-supplied FL code inside the trust's fl-client
    reaches it through ``flip.get_dataframe(...)``, so it necessarily returns row-level
    records — a model trains on rows. The data stays inside the trust; only model updates
    leave it.

    It runs the query of record stored at approval (FLIP#857) — **the caller-supplied SQL is
    ignored** (logged when it differs) — against live OMOP, uncached, and keeps only the rows
    whose ``person_id`` / ``accession_id`` are in the membership frozen at approval. So the
    cohort can shrink (a patient removed from OMOP drops out on the next fetch) but can never
    grow, and researcher code cannot execute arbitrary SQL under an approved project's id. A
    project with no frozen membership is refused.

    Because it is row-level, the served cohort must clear the disclosure threshold (the
    governance decision's ``effective_threshold``) in **distinct subjects**, re-counted on every
    fetch: a cohort that shrinks below the floor stops being served.

    Note there is deliberately no column allowlist here. ``accession_id`` is
    load-bearing — it is how the returned rows join to the imaging studies
    pulled into XNAT — and shipped tutorials legitimately select ``*``, so a
    column filter would break every FL app on the platform while a caller could
    trivially alias around it. Column-level minimisation belongs in the cohort
    query the project submits and in project approval, not here.

    Args:
        query_input (DataframeQuery): The encrypted project id (and the advisory query).

    Returns:
        dict[str, list[Any]]: The approved cohort in a DataFrame-like structure.

    Raises:
        HTTPException: 400 if the encrypted project id cannot be opened, 403 if policy denies
            the project, it has no frozen membership, or the served cohort covers fewer subjects
            than the disclosure threshold, 500 if the query fails to execute.
    """
    project_id = _open_project_id(query_input.encrypted_project_id)

    logger.info(f"Received DataFrame query for project {project_id}")

    decision = _evaluate(ACTION_COHORT_DATAFRAME, project_id)
    if not decision.permit:
        _log_denial(decision, project_id, "row-level data")
        # Same fixed text as a below-threshold refusal, deliberately: a distinct message
        # for "policy denied" would confirm the project exists and reveal that a rule
        # covers it, and would separate a denied cohort from an empty one.
        raise HTTPException(status_code=403, detail=_BELOW_THRESHOLD_DETAIL)

    snapshot = _require_snapshot(project_id)
    _log_ignored_client_query(project_id, snapshot, query_input.query)

    # Uncached: a removal from OMOP must reach training on the next fetch, not after
    # CACHE_TTL_DAYS.
    df = _restrict_to_membership(_run_cohort_query(snapshot.query, use_cache=False, what="DataFrame"), snapshot)
    _check_threshold(project_id, _count_subjects_or_zero(df, project_id), decision.effective_threshold)

    logger.info(f"Serving approved cohort for project {project_id}: {len(df)} rows")
    return df.to_dict(orient="list")


@read_router.post("/accession-ids", response_model=AccessionIdsResponse)
def get_accession_ids(query_input: DataframeQuery) -> AccessionIdsResponse:
    """
    Returns the approved cohort's accession IDs that are still imaging studies in OMOP.

    This is the minimal-disclosure endpoint used by imaging-api to fetch the accession
    numbers it needs to import studies from PACS — it does not expose row-level patient
    attributes. It never runs the cohort SQL: it takes the accession ids frozen at approval
    (FLIP#857) and keeps those that still resolve through ``omop.image_occurrence``
    (``keep_imaging_accessions``), read uncached, so the pointer set cannot grow and a study
    removed from OMOP drops out on the next call. The imaging status poll (roughly every 10 s while
    a project page is open) therefore costs two lookups on ``image_occurrence`` rather than a
    cohort query. Values that never resolved to an
    imaging study are never released, so nothing can ride out under the ``accession_id`` alias
    past a ``cohort.dataframe`` deny (FLIP#1259).

    Accession IDs are still row-level identifiers, and they are the pointer set into the imaging
    data: they decide whose studies get pulled into XNAT, where project members view them. So
    the released set must clear the disclosure threshold in **distinct subjects** (resolved
    through ``omop.image_occurrence``, never more than the number of values released), and the
    refusal reuses ``/cohort/dataframe``'s fixed text so an empty set and a below-threshold one
    are indistinguishable. The trust applies this itself rather than relying on the hub's
    staging guard — a trust must stay safe regardless of what the hub checked.

    A cohort frozen without an ``accession_id`` column, or whose ``accession_id`` was NULL on every
    row, returns an EMPTY list rather than an error: it has no imaging to pull.

    Args:
        query_input (DataframeQuery): The encrypted project id (and the advisory query).

    Returns:
        AccessionIdsResponse: The approved cohort's current imaging accession IDs.

    Raises:
        HTTPException: 400 if the encrypted project id cannot be opened, 403 if policy denies
            the project, it has no frozen membership, or the released set covers fewer subjects
            than the disclosure threshold.
    """
    project_id = _open_project_id(query_input.encrypted_project_id)

    logger.info(f"Received accession-ids query for project {project_id}")

    decision = _evaluate(ACTION_COHORT_ACCESSION_IDS, project_id)
    if not decision.permit:
        _log_denial(decision, project_id, "accession IDs")
        # Byte-identical to the below-threshold refusal, as on /cohort/dataframe.
        raise HTTPException(status_code=403, detail=_BELOW_THRESHOLD_DETAIL)

    snapshot = _require_snapshot(project_id)
    _log_ignored_client_query(project_id, snapshot, query_input.query)

    if not snapshot.accession_ids:
        # Threshold before the shape answer: nothing about a below-threshold cohort is revealed.
        _check_threshold(project_id, snapshot.subject_count, decision.effective_threshold)
        logger.info(f"Approved cohort for project {project_id} froze no accession ids (tabular project)")
        return AccessionIdsResponse(accession_ids=[])

    # Guarded like the count: a lookup failure must be indistinguishable from a small cohort.
    try:
        imaging = keep_imaging_accessions(
            pd.DataFrame({ACCESSION_ID_COLUMN: list(snapshot.accession_ids)}), use_cache=False
        )
    except Exception:
        logger.exception(f"Imaging accession lookup failed for project {project_id}; refusing as below threshold")
        imaging = pd.DataFrame({ACCESSION_ID_COLUMN: []})
    _check_threshold(project_id, _count_subjects_or_zero(imaging, project_id), decision.effective_threshold)

    accession_ids = [str(value) for value in imaging[ACCESSION_ID_COLUMN].tolist()]
    logger.info(f"Serving {len(accession_ids)} approved accession ids for project {project_id}")
    return AccessionIdsResponse(accession_ids=accession_ids)


@write_router.post("/snapshot", response_model=SnapshotResponse)
def create_snapshot(query_input: SnapshotCreateRequest) -> SnapshotResponse:
    """
    Runs the cohort ONCE and freezes its membership as this project's approved cohort (FLIP#857).

    Called by trust-api when the hub approves a project. It defines the cohort everyone then
    trains on, so it sits on the ``write_router`` and requires cohort-admin authorisation
    (proof of possessing ``AES_KEY_BASE64``) in addition to the trust-internal key — fl-client
    holds the latter but not the former, so researcher code cannot reach here. What is stored is
    the query of record and the ``person_id`` / ``accession_id`` values it returned — no
    clinical values. From that point the row-level routes run only the stored query, restricted
    to those ids, so the cohort can shrink but never grow.

    Freezing is once per project: when a membership already exists the route returns its facts
    without running the query again. A snapshot the hub re-queues — because its result never
    arrived, or to re-check a trust that may have lost its store — therefore cannot re-admit
    patients the frozen membership excludes; only a project whose record is absent (never frozen,
    deleted, or lost with the store) is frozen afresh from live OMOP.

    The trust's governance policy is asked first, as for ``cohort.statistics``: the freeze reports
    the same aggregates to the hub (row and subject counts), so a project denied statistics is not
    frozen, and the threshold applied is the decision's ``effective_threshold`` — a policy
    ``min_cohort_size`` raise holds here exactly as it does on the statistics route.

    The run is uncached, so it freezes LIVE OMOP (the statistics run at submission cached the
    same SQL for ``CACHE_TTL_DAYS``). ``validate_query`` remains the authority on the SQL
    executed. The disclosure threshold is enforced BEFORE anything is persisted: a
    below-threshold cohort leaves no record and returns the same fixed refusal as the row-level
    routes, and a cohort whose subjects cannot be established — it projects neither
    ``person_id`` nor ``accession_id``, so it has no membership to freeze — is a 400 naming the
    column (that reports the query's SHAPE, never its contents). The response carries aggregates
    only.

    Args:
        query_input (SnapshotCreateRequest): The approved cohort query and encrypted project id.

    Returns:
        SnapshotResponse: What was frozen — the existing record's facts when one was kept.

    Raises:
        HTTPException: 400 if the query is invalid, exposes neither ``person_id`` nor
            ``accession_id``, or the project id cannot be opened or is not a UUID, 403 if policy
            denies the project or the cohort covers fewer subjects than the disclosure threshold, 413 if the serialized
            record exceeds ``SNAPSHOT_MAX_BYTES``, 500 if the query fails to execute or a frozen record
            exists but cannot be read (nothing is written over it), 503 if the snapshot store is not
            configured.
    """
    if not snapshot_enabled():
        raise HTTPException(status_code=503, detail="Cohort snapshot store is not configured on this trust.")

    project_id = _open_project_id(query_input.encrypted_project_id)
    logger.info(f"Received cohort snapshot request for project {project_id}")
    try:
        uuid.UUID(project_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Project id is not a valid UUID.")

    # Decided before the frozen record is read, so a denied project answers the same whether or not
    # it holds a membership, and a denial never runs the query.
    decision = _evaluate(ACTION_COHORT_STATISTICS, project_id)
    if not decision.permit:
        _log_denial(decision, project_id, "cohort snapshot")
        raise HTTPException(status_code=403, detail=_BELOW_THRESHOLD_DETAIL)

    existing = _load_for_freeze(project_id)
    if existing is not None:
        return _kept_snapshot_response(project_id, existing, query_input.query)

    df = _run_cohort_query(query_input.query, use_cache=False, what="Snapshot cohort")

    # The count sits outside get_records' handling on purpose: its 400s keep their diagnostic
    # shape, while a failure of the count itself is refused exactly as a small cohort is.
    try:
        subject_count = count_distinct_subjects(df, use_cache=False)
    except Exception:
        logger.exception(f"Subject count unavailable for project {project_id}; refusing as below threshold")
        subject_count = 0
    if subject_count is None:
        logger.warning(
            f"Refusing to snapshot project {project_id}: cohort exposes neither person_id nor "
            "accession_id, so its subject count cannot be established"
        )
        raise HTTPException(status_code=400, detail=_UNCOUNTABLE_SUBJECTS_DETAIL)
    _check_threshold(project_id, subject_count, decision.effective_threshold)

    try:
        snapshot = save_snapshot(
            project_id,
            query=query_input.query,
            person_ids=_frozen_ids(df, SUBJECT_ID_COLUMN),
            accession_ids=_frozen_ids(df, ACCESSION_ID_COLUMN),
            row_count=len(df),
            subject_count=subject_count,
            columns=[str(column) for column in df.columns],
        )
    except SnapshotExists:
        # Another freeze for this project landed while the query ran: the first one stands.
        frozen = _load_for_freeze(project_id)
        if frozen is None:
            logger.error(f"Cohort membership for project {project_id} vanished after a concurrent freeze")
            raise HTTPException(status_code=500, detail="Snapshot persistence failed.")
        return _kept_snapshot_response(project_id, frozen, query_input.query)
    except ValueError:
        logger.exception(f"Cohort membership for project {project_id} failed validation")
        raise HTTPException(status_code=500, detail="Snapshot persistence failed.")
    except SnapshotTooLarge as err:
        logger.error(str(err))
        raise HTTPException(status_code=413, detail="Cohort snapshot exceeds the configured size limit.")
    except OSError:
        logger.exception("Cohort snapshot store write failed")
        raise HTTPException(status_code=500, detail="Snapshot persistence failed.")

    return _snapshot_response(snapshot)


def _load_for_freeze(project_id: str) -> Snapshot | None:
    """The project's frozen membership for a freeze request: None only when there is no record.

    A record that exists but cannot be read is a 500 and nothing is written. Treating it as absent
    would re-run the query and write today's live cohort over the approved one, so a read blip during a
    re-queued snapshot would let the cohort grow.
    """
    try:
        return load_snapshot(project_id)
    except SnapshotUnreadable:
        logger.exception(f"Refusing to freeze project {project_id}: its frozen membership cannot be read")
        raise HTTPException(status_code=500, detail="Cohort snapshot store read failed.")


def _kept_snapshot_response(project_id: str, snapshot: Snapshot, requested_query: str) -> SnapshotResponse:
    """The facts of a membership already frozen, which a freeze request keeps rather than replaces."""
    if normalised_query_hash(requested_query) != snapshot.query_hash:
        logger.warning(
            f"Snapshot request for project {project_id} carries a different query from the frozen one — "
            "kept the frozen membership"
        )
    logger.info(f"Cohort membership for project {project_id} is already frozen; returning its facts")
    return _snapshot_response(snapshot)


def _snapshot_response(snapshot: Snapshot) -> SnapshotResponse:
    """The aggregates-only facts of a frozen membership."""
    return SnapshotResponse(
        row_count=snapshot.row_count,
        columns=list(snapshot.columns),
        has_accessions=snapshot.has_accessions,
        snapshot_at=snapshot.created_at,
        query_hash=snapshot.query_hash,
    )


@write_router.post("/snapshot/delete")
def remove_snapshot(query_input: SnapshotDeleteRequest) -> dict[str, bool]:
    """
    Removes a project's frozen cohort membership. Idempotent.

    The teardown hook for the project purge path (FLIP#997 — which has no hub-side caller
    yet, so nothing invokes this in the current lifecycle). After deletion the project's
    row-level routes refuse until a fresh snapshot is frozen.

    Args:
        query_input (SnapshotDeleteRequest): The encrypted project id.

    Returns:
        dict[str, bool]: ``{"deleted": bool}`` — False when no snapshot existed.
    """
    project_id = _open_project_id(query_input.encrypted_project_id)
    deleted = delete_snapshot(project_id)
    logger.info(f"Snapshot delete for project {project_id}: {'removed' if deleted else 'nothing to remove'}")
    return {"deleted": deleted}
