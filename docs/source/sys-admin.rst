.. _sys-admin:

######################
System administration
######################

.. toctree::
   :maxdepth: 2

   sys-admin/admin-project-and-user-management
   sys-admin/admin-user-roles
   sys-admin/admin-platform-support

.. seealso::

   - Deploying a FLIP node inside a TRE: :doc:`/deploy-flip/deploy-flip-node-in-tre`
   - Logging & observability stack: :doc:`/components/component-logging-stack`

.. note::

   For deployments on **Kubernetes**, see the Helm chart at
   ``deploy/providers/kubernetes/`` and the
   `K8s README <https://github.com/londonaicentre/FLIP/blob/develop/deploy/providers/kubernetes/README.md>`_
   for operational notes, troubleshooting, and configuration reference.

.. _logging-policy:

**************
Logging policy
**************

FLIP handles patient-level data, and log pipelines have far weaker access
control than the data paths they sit beside: trust logs are aggregated into the
observability stack, hub logs into CloudWatch, and FL run logs can be read by
model developers outside the trust. Every service therefore follows one rule
set for what may appear in a log line, at **every** log level — ``DEBUG``
included, since a dev-tuned log level must never change what can leak.

What is never logged
====================

**Cohort SQL.** A cohort query encodes patient-level selection criteria.
Log its *fingerprint* instead: the SHA-256 of the whitespace-normalised,
lower-cased query, truncated to 12 hex chars (``query sha256:ab12…``). Every
service carries the same helper (``utils/log_hygiene.py`` in flip-api,
data-access-api, and imaging-api) with the same normalisation, so hub and
trust log lines for one query carry one fingerprint — and because the hub
stores every cohort query, an operator can re-hash the stored SQL to find the
log lines it produced. This also means raw driver/parser error text
(psycopg2's ``LINE 1:`` context, SQLAlchemy's ``[SQL: …]`` suffix, sqlglot's
quoted fragment) never reaches a log — log the exception class, the SQLSTATE
where available, and the fingerprint. Both SQLAlchemy engines are additionally
built with ``hide_parameters=True`` so bound parameter values never render in
wrapped driver errors.

**Accession numbers and any patient-level identifier or attribute.** Log
ordinals (``accession 3/17``), counts, or a fingerprint
(``flip.utils.Utils.hash_for_log``) — never the value. Response *bodies* from
the trust data and imaging APIs are patient-level material (a cohort dataframe,
DQR study metadata) and are never logged; log the status code and row/object
counts only.

**Secrets and credentials.** No request headers (they carry the trust-internal
service key and XNAT tokens), no request bodies, no presigned URLs in any form.

**Full URLs.** Log scheme-less host + path only; the query string is always
dropped (``encoded_query`` is base64-wrapped SQL). Exception text from HTTP
client libraries interpolates the full URL, so those messages are reduced to
the exception class before logging.

**S3 object keys.** Log the bucket plus either a SHA-256 prefix of the key
(``flip_api.utils.s3_client.hash_s3_key``, which hashes exact bytes — keys are
case-sensitive) or the platform identifiers the key is derived from (model id,
uploaded file name — both already user-visible metadata). Never the verbatim
key or full ``s3://`` path, and never a presigned form of it.

Working with scrubbed logs
==========================

Fingerprints are correlation handles, not redaction theatre: the party that
legitimately holds a value (the hub's stored cohort SQL, a model developer's
own accession list, a caller's S3 key) can re-derive the fingerprint and grep
for it. When you need more context than a class name — for example a failing
cohort query — re-run the stored query against the target system rather than
relaxing a log line.

HTTP **error response bodies** are governed separately (category-only details;
see the cohort validation notes in
`trust/data-access-api/README.md <https://github.com/londonaicentre/FLIP/blob/develop/trust/data-access-api/README.md>`_)
— this policy covers what lands in logs. When adding a log line, assume it will
be read by someone who must not see patient data, and pin the behaviour with a
unit test asserting the sensitive value stays out of ``caplog`` (see
``tests/services/test_cohort.py`` in data-access-api for the pattern).
