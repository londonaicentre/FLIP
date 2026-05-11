# Progress

## Status

In Progress

## Tasks

- [x] Update issue-379-report.md (Stateful services) — re-verified all 10 ACs, added secrets refactor note
- [x] Update issue-381-report.md (Observability stack) — re-verified all ACs, noted image repo migration to ghcr.io/*
- [x] Update issue-380-report.md (XNAT multicontainer) — re-verified all 12 ACs, no XNAT files changed by recent commits, updated verification date
- [x] Update issue-377-report.md — re-verified all 14 ACs against current templates (secrets.yaml, trust-api.yaml, imaging-api.yaml, data-access-api.yaml, test_helm_chart.yml, values.schema.json). Updated AC 13 (CI workflow exists), AC 14 (kind-e2e pipeline confirmed, no squashed `|| true`, still no hub polling in CI). Added imagePullSecrets fix, orthanc-registered-users refactor, PVC fallback, heartbeat boundary notes.
- [x] Update issue-378-report.md — re-verified all 10 ACs against fl-client.yaml, secrets.yaml, values.yaml, test-secret.yaml at commit 5eb3a4a3. All still DONE, no gaps remaining.

- [x] Update issue-382-report.md — re-verified all 28 ACs. Key updates: NP2 (egress ports now configurable in values.yaml), MF5 (add-k8s-trust doesn't pass --trust-number minor gap), SM3 (|| true removed from kubectl wait), SM4 (still missing), .gitignore path fix noted.
- [x] Update issue-420-report.md — re-verified all 11 ACs, AC 11 (XNAT external) still partial. Updated evidence: AC 2 (ghcr.io/grafana observability images), AC 6 (configurable egress ports, port 80/443 in defaults), AC 9 (|| true removed from kubectl wait), AC 7 (add-k8s-trust target confirmed). Updated verification date and key fixes delivered.

## Files Changed

- .plans/reports/issue-381-report.md — updated verification date, added image repo migration note, re-verified all 8 ACs
- .plans/reports/issue-379-report.md — re-verified all 10 ACs against current templates, added secrets refactor note (orthanc-registered-users), expanded AC detail with tables
- .plans/reports/issue-382-report.md — re-verified all 28 ACs, updated verification date, added recent improvements table, noted egress port 80/443 migration to values.yaml defaults, CI strictness improvement, MF5 --trust-number gap
- .plans/reports/issue-420-report.md — re-verified all 11 ACs, updated verification date to 2026-05-11, corrected AC 2 (ghcr.io image paths), AC 6 (configurable egress ports), AC 9 (kubectl wait || true removed), added key fixes: image migration, egress port migration, CI strictness

## Notes

- Issue #379 report was already 10/10 DONE. Re-verified all source files (omop-db.yaml, orthanc.yaml, omop-db-init-job.yaml, secrets.yaml, values.yaml). Added orthanc secrets refactor (orthanc-username/password → orthanc-registered-users) as key fix from commit 5eb3a4a3. All ACs confirmed solid.
- Issue #380 report re-verified after commits e14dfc43, 5eb3a4a3. No XNAT templates changed. XNAT secrets (xnat-admin-password, xnat-service-user, xnat-service-password, xnat-datasource-password) unchanged. imagePullSecrets helper fix applies to all templates but renders identical output. AC 7 (external XNAT fallback) remains the only partial.
