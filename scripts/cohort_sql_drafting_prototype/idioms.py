# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
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
# ruff: noqa: E501  (one SQL probe per line reads best)
"""Probe the trust validator with common, legitimate OMOP cohort idioms (no API calls)."""

import helper

B = "SELECT co.person_id, {x} AS v FROM omop.condition_occurrence co ORDER BY co.person_id LIMIT 5"
IDIOMS = {
    "ROW_NUMBER window": "ROW_NUMBER() OVER (PARTITION BY co.person_id ORDER BY co.condition_start_date)",
    "LAG window": "LAG(co.condition_start_date) OVER (PARTITION BY co.person_id ORDER BY co.condition_start_date)",
    "MIN() OVER": "MIN(co.condition_start_date) OVER (PARTITION BY co.person_id)",
    "date - INTERVAL": "co.condition_start_date - INTERVAL '365 days'",
    "date - integer": "co.condition_start_date - 365",
    "date - date": "co.condition_end_date - co.condition_start_date",
    "::date cast": "co.condition_start_date::date",
    "CAST AS DATE": "CAST(co.condition_start_date AS DATE)",
    "DATE literal": "DATE '2020-01-01'",
    "CURRENT_DATE": "CURRENT_DATE",
    "NOW()": "NOW()",
    "EXTRACT(YEAR)": "EXTRACT(YEAR FROM co.condition_start_date)",
    "EXTRACT(EPOCH)": "EXTRACT(EPOCH FROM co.condition_start_date)",
    "date_part": "date_part('year', co.condition_start_date)",
    "date_trunc": "date_trunc('month', co.condition_start_date)",
    "AGE()": "AGE(co.condition_start_date, DATE '1970-01-01')",
    "make_date": "make_date(2020, 1, 1)",
    "make_interval": "make_interval(days => 90)",
    "COALESCE/NULLIF": "COALESCE(NULLIF(co.condition_source_value, ''), 'x')",
    "LEAST/GREATEST": "LEAST(co.condition_start_date, co.condition_end_date)",
    "COUNT FILTER": "COUNT(*) FILTER (WHERE co.condition_source_value = '1') OVER ()",
    "string_agg": "string_agg(co.condition_source_value, ',') OVER ()",
    "ABS/ROUND": "ROUND(ABS(co.condition_end_date - co.condition_start_date) / 7.0, 1)",
    "LOWER/LIKE": "LOWER(co.condition_source_value) LIKE 'e11%'",
    "SUBSTRING": "SUBSTRING(co.condition_source_value FROM 1 FOR 3)",
    "left()": "LEFT(co.condition_source_value, 3)",
    "to_char": "to_char(co.condition_start_date, 'YYYY')",
    "to_date": "to_date('2020-01-01', 'YYYY-MM-DD')",
    "percentile_cont": "percentile_cont(0.5) WITHIN GROUP (ORDER BY co.condition_occurrence_id) OVER ()",
}
WHOLE = {
    "DISTINCT ON": "SELECT DISTINCT ON (io.person_id) io.person_id, io.accession_id FROM omop.image_occurrence io ORDER BY io.person_id, io.image_occurrence_date LIMIT 5",
    "LATERAL subquery": "SELECT p.person_id, f.accession_id FROM omop.person p CROSS JOIN LATERAL (SELECT io.accession_id FROM omop.image_occurrence io WHERE io.person_id = p.person_id ORDER BY io.image_occurrence_date LIMIT 1) f ORDER BY p.person_id LIMIT 5",
    "UNION": "SELECT person_id FROM omop.condition_occurrence UNION SELECT person_id FROM omop.drug_exposure ORDER BY 1 LIMIT 5",
    "EXCEPT (exclusion)": "SELECT person_id FROM omop.condition_occurrence EXCEPT SELECT person_id FROM omop.death ORDER BY 1 LIMIT 5",
    "NOT EXISTS": "SELECT p.person_id FROM omop.person p WHERE NOT EXISTS (SELECT 1 FROM omop.death d WHERE d.person_id = p.person_id) ORDER BY p.person_id LIMIT 5",
    "WINDOW clause": "SELECT co.person_id, ROW_NUMBER() OVER w AS rn FROM omop.condition_occurrence co WINDOW w AS (PARTITION BY co.person_id ORDER BY co.condition_start_date) ORDER BY 1 LIMIT 5",
    "recursive CTE": "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < 3) SELECT p.person_id, n.i FROM omop.person p CROSS JOIN n ORDER BY 1 LIMIT 5",
    "generate_series": "SELECT p.person_id, g.y FROM omop.person p CROSS JOIN generate_series(2018, 2020) AS g(y) ORDER BY 1 LIMIT 5",
    "no LIMIT": "SELECT person_id FROM omop.person ORDER BY person_id",
    "LIMIT ALL": "SELECT person_id FROM omop.person ORDER BY person_id LIMIT ALL",
    "FETCH FIRST": "SELECT person_id FROM omop.person ORDER BY person_id FETCH FIRST 5 ROWS ONLY",
    "trailing semicolon": "SELECT person_id FROM omop.person ORDER BY person_id LIMIT 5;",
    "OVERLAPS": "SELECT op.person_id FROM omop.observation_period op WHERE (op.observation_period_start_date, op.observation_period_end_date) OVERLAPS (DATE '2020-01-01', DATE '2021-01-01') LIMIT 5",
}
for name, x in IDIOMS.items():
    r = helper.validate(B.format(x=x))
    print(f"{name:22} {'ok' if r['ok'] else r.get('stage', '') + ': ' + r['error']}")
for name, q in WHOLE.items():
    r = helper.validate(q)
    print(f"{name:22} {'ok' if r['ok'] else r.get('stage', '') + ': ' + r['error']}")
