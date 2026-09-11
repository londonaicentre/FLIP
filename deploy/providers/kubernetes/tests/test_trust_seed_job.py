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
"""The trust-seed hook replaced the pgdata / Orthanc volume snapshots (FLIP#1187).

Text-level guards, like the other chart tests (``helm`` is not on the pytest job's runner, and
``helm template`` only reaches the branches its values enable). Each pins one way the snapshot
path could creep back or the seed could silently stop covering a store.
"""

import re
from pathlib import Path

import yaml

CHART_DIR = Path(__file__).resolve().parents[1]
TEMPLATES = CHART_DIR / "templates"
SEED_JOB = TEMPLATES / "trust-seed-job.yaml"
VOCAB_JOB = TEMPLATES / "omop-db-vocab-load-job.yaml"
VALUES = yaml.safe_load((CHART_DIR / "values.yaml").read_text())


def _hook_weight(template: Path) -> int:
    match = re.search(r'"helm.sh/hook-weight": "(-?\d+)"', template.read_text())
    assert match, f"{template.name} carries no hook weight"
    return int(match.group(1))


def test_the_snapshot_restore_is_gone_from_every_template():
    """No template downloads or untars a trust<N>_pgdata / _orthanc_data volume any more."""
    for template in TEMPLATES.glob("*.yaml"):
        text = template.read_text()
        assert "_pgdata" not in text and "_orthanc_data" not in text, template.name
        assert "restore-data" not in text and "seed-data" not in text, template.name
    assert not (TEMPLATES / "omop-db-init-job.yaml").exists()
    assert not (TEMPLATES / "orthanc-init-job.yaml").exists()


def test_omop_db_and_orthanc_start_on_empty_volumes():
    """Neither StatefulSet/Deployment carries an initContainer: the stores are filled by the hook."""
    for name in ("omop-db.yaml", "orthanc.yaml"):
        assert "initContainers" not in (TEMPLATES / name).read_text(), name


def test_orthanc_owns_its_fresh_pvc_via_fsgroup():
    """Orthanc runs as uid 999 and the initContainer that used to chown the volume is gone."""
    text = (TEMPLATES / "orthanc.yaml").read_text()
    assert re.search(r"securityContext:\n\s+fsGroup: 999", text)


def test_seed_runs_after_the_vocab_load():
    """Weight 6 after 5: rows land in the constrained database the vocab load leaves behind."""
    assert _hook_weight(SEED_JOB) > _hook_weight(VOCAB_JOB)
    assert "post-install,post-upgrade" in SEED_JOB.read_text()


def test_seed_job_runs_the_shared_script_at_a_pinned_ref():
    """The Job fetches trust/seed_trust.sh (the one procedure EC2 runs too) at trustData.seed.sourceRef."""
    text = SEED_JOB.read_text()
    assert "raw.githubusercontent.com/londonaicentre/FLIP/${FLIP_REF}/trust/seed_trust.sh" in text
    assert "exec bash /work/seed_trust.sh" in text
    assert 'required "trustData.seed.sourceRef is required' in text
    for env in ("TRUST_DATA_VERSION", "SOURCE_TRUST", "NUM_TRUSTS", "PROJECTS", "SEED_OMOP", "SEED_ORTHANC", "ORTHANC_URL", "WORK_DIR"):
        assert f"- name: {env}\n" in text, env


def test_the_shared_script_installs_the_repository_loaders_without_git():
    """trust/seed_trust.sh: omop_db_tools from a source archive (no git in the image), seed_orthanc.py by URL."""
    script = (CHART_DIR.parents[2] / "trust" / "seed_trust.sh").read_text()
    assert "https://github.com/londonaicentre/FLIP/archive/${FLIP_REF}.tar.gz#subdirectory=trust/omop-db" in script
    assert "raw.githubusercontent.com/londonaicentre/FLIP/${FLIP_REF}/trust/orthanc/seed_orthanc.py" in script
    assert "omop_db_tools.load_dicom_vocab --vocab-dir" in script and "--skip-if-loaded" in script
    assert "--clean projects" in script and "--clean all" not in script
    assert "git+" not in script


def test_seed_values_defaults():
    """Defaults: OMOP on, Orthanc off (2 GB of DICOM), the two dev projects, partition = trustNumber."""
    seed = VALUES["trustData"]["seed"]
    assert seed["enabled"] is True and seed["omop"] is True and seed["orthanc"] is False
    assert seed["projects"] == "cxr_project spleen_project"
    assert seed["sourceTrust"] == "" and seed["numTrusts"] == 2
    assert seed["sourceRef"]
    assert "initJob" not in VALUES["omopDb"] and "initJob" not in VALUES["orthanc"]


def test_vocab_load_credentials_live_under_vocab_load():
    """The vocab-load Job borrowed omopDb.initJob's AWS keys; with initJob gone they are its own."""
    text = VOCAB_JOB.read_text()
    assert ".Values.omopDb.initJob" not in text
    assert ".Values.omopDb.vocabLoad.hostAwsMount" in text and ".Values.omopDb.vocabLoad.awsProfile" in text
    assert "hostAwsMount" in VALUES["omopDb"]["vocabLoad"] and "awsProfile" in VALUES["omopDb"]["vocabLoad"]
