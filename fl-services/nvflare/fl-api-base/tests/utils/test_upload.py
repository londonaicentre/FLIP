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

import json
from unittest.mock import MagicMock, patch

import pytest
import requests
from fastapi import HTTPException

from fl_api.utils.refusals import ResearcherRefusal
from fl_api.utils.schemas import JobResources, UploadAppRequest
from fl_api.utils.upload import upload_application, validate_config

TEST_MODEL_ID = "c7f72374-0752-473f-a28f-592e4d8b7a47"
TMP_PATH_UPLOAD_DIR = "/tmp/tests/_upload_dir"


# Tests in upload_app.
@pytest.fixture
def mock_upload_correct_request():
    base = f"https://test.local/bundles/{TEST_MODEL_ID}"
    return UploadAppRequest(
        bundle_urls=[
            f"{base}/app/config/config_fed_server.json",
            f"{base}/app/config/config_fed_client.json",
            f"{base}/app/custom/config.json",
            f"{base}/app/custom/trainer.py",
            f"{base}/app/custom/validator.py",
            f"{base}/app/custom/models.py",
        ],
        project_id="123456789",
        cohort_query="SELECT * FROM patients WHERE age > 60",
        trusts=["Trust_1", "Trust_2"],
    )


@pytest.fixture
def mock_upload_multiple_apps_request():
    base = f"https://test.local/bundles/{TEST_MODEL_ID}"

    return UploadAppRequest(
        bundle_urls=[
            # Default app
            f"{base}/app/config/config_fed_server.json",
            f"{base}/app/config/config_fed_client.json",
            f"{base}/app/custom/trainer.py",
            f"{base}/app/custom/models.py",
            # Per-trust app: Trust 1
            f"{base}/app-trust1/config/config_fed_server.json",
            f"{base}/app-trust1/config/config_fed_client.json",
            f"{base}/app-trust1/custom/trainer.py",
            f"{base}/app-trust1/custom/models.py",
            # Per-trust app: Trust 2
            f"{base}/app-trust2/config/config_fed_server.json",
            f"{base}/app-trust2/config/config_fed_client.json",
            f"{base}/app-trust2/custom/trainer.py",
            f"{base}/app-trust2/custom/models.py",
            # Meta file for multiple app folders
            f"{base}/meta.json",
        ],
        project_id="123456789",
        cohort_query="SELECT * FROM patients WHERE age > 60",
        trusts=["Trust_1", "Trust_2"],
    )


@pytest.fixture
def mock_upload_multiple_apps_request_missing_meta_json():
    base = f"https://test.local/bundles/{TEST_MODEL_ID}"

    return UploadAppRequest(
        bundle_urls=[
            # Default app
            f"{base}/app/config/config_fed_server.json",
            f"{base}/app/config/config_fed_client.json",
            f"{base}/app/custom/trainer.py",
            f"{base}/app/custom/models.py",
            # Per-trust app: Trust 1
            f"{base}/app-trust1/config/config_fed_server.json",
            f"{base}/app-trust1/config/config_fed_client.json",
            f"{base}/app-trust1/custom/trainer.py",
            f"{base}/app-trust1/custom/models.py",
            # Per-trust app: Trust 2
            f"{base}/app-trust2/config/config_fed_server.json",
            f"{base}/app-trust2/config/config_fed_client.json",
            f"{base}/app-trust2/custom/trainer.py",
            f"{base}/app-trust2/custom/models.py",
            # ❌ Intentionally missing:
            # f"{base}/meta.json"
        ],
        project_id="123456789",
        cohort_query="SELECT * FROM patients WHERE age > 60",
        trusts=["Trust_1", "Trust_2"],
    )


@pytest.fixture
def mock_requests_get_success():
    """
    Patches fl_api.utils.upload.requests.get so it always returns a response
    with `.content` (and optional `.raise_for_status`).
    """
    with patch("fl_api.utils.upload.requests.get") as mock_get:
        resp = MagicMock()
        resp.content = b"mock file content"
        resp.raise_for_status = MagicMock()
        resp.is_redirect = False
        resp.is_permanent_redirect = False
        # _stage_server_checkpoint streams via `with requests.get(...) as resp:`, so the
        # response is bound from __enter__(); point it back at the same configured mock so the
        # streaming path sees is_redirect=False too (the plain path uses the return_value directly).
        resp.__enter__.return_value = resp
        resp.iter_content.return_value = [resp.content]
        mock_get.return_value = resp
        yield mock_get


def test_error_requests_get(
    mock_requests_get_success,
    mock_upload_correct_request,
):
    # Test when requests.get fails with an error
    mock_requests_get_success.side_effect = Exception("Network error")

    with pytest.raises(Exception, match="Network error") as exc_info:
        _ = upload_application(TEST_MODEL_ID, mock_upload_correct_request, TMP_PATH_UPLOAD_DIR)

    assert "Network error" in str(exc_info.value)


@patch("fl_api.utils.upload.configure_environment", MagicMock())
@patch("fl_api.utils.upload.configure_meta", MagicMock())
@patch("fl_api.utils.upload.configure_server", MagicMock())
@patch("fl_api.utils.upload.configure_client", MagicMock())
@patch("fl_api.utils.upload.configure_config", MagicMock())
@patch("fl_api.utils.upload.validate_config", MagicMock())
@patch("fl_api.utils.upload.read_config", MagicMock(return_value={}))
@patch("fl_api.utils.upload.configure_flip_model_id", MagicMock())
def test_upload_app_success(
    mock_requests_get_success,
    mock_upload_correct_request,
):
    response = upload_application(TEST_MODEL_ID, mock_upload_correct_request, TMP_PATH_UPLOAD_DIR)

    assert "Application uploaded successfully" in response["message"]


@patch("fl_api.utils.upload.configure_environment", MagicMock())
@patch("fl_api.utils.upload.configure_meta", MagicMock())
@patch("fl_api.utils.upload.configure_server", MagicMock())
@patch("fl_api.utils.upload.configure_client", MagicMock())
@patch("fl_api.utils.upload.configure_config", MagicMock())
@patch("fl_api.utils.upload.validate_config", MagicMock())
@patch("fl_api.utils.upload.read_config", MagicMock(return_value={}))
@patch("fl_api.utils.upload.configure_flip_model_id", MagicMock())
def test_upload_multiple_apps_success(
    mock_requests_get_success,
    mock_upload_multiple_apps_request,
):
    response = upload_application(TEST_MODEL_ID, mock_upload_multiple_apps_request, TMP_PATH_UPLOAD_DIR)

    assert "Application uploaded successfully" in response["message"]


def test_upload_multiple_apps_missing_meta_json(
    mock_requests_get_success,
    mock_upload_multiple_apps_request_missing_meta_json,
):
    with pytest.raises(ResearcherRefusal) as exc_info:
        _ = upload_application(TEST_MODEL_ID, mock_upload_multiple_apps_request_missing_meta_json, TMP_PATH_UPLOAD_DIR)

    assert "Application must contain a meta.json file" in str(exc_info.value)
    assert exc_info.value.status_code == 404


@patch("fl_api.utils.upload.configure_environment", MagicMock())
@patch("fl_api.utils.upload.configure_meta", MagicMock())
@patch("fl_api.utils.upload.configure_server", MagicMock())
@patch("fl_api.utils.upload.configure_client", MagicMock())
@patch("fl_api.utils.upload.configure_config", MagicMock())
@patch("fl_api.utils.upload.validate_config", MagicMock())
@patch("fl_api.utils.upload.read_config", MagicMock(return_value={}))
@patch("fl_api.utils.upload.configure_flip_model_id", MagicMock())
def test_upload_stages_server_checkpoint_off_job(mock_requests_get_success, tmp_path, monkeypatch):
    """A bundle file under ``/server_checkpoints/`` is staged on the shared checkpoint volume
    (``SERVER_CHECKPOINT_ROOT/<model_id>/<file>``), NOT placed in the FLARE job dir — so it is
    never part of the submitted job and never deployed to clients."""
    ckpt_root = tmp_path / "server-checkpoints"
    job_root = tmp_path / "jobs"
    monkeypatch.setenv("SERVER_CHECKPOINT_ROOT", str(ckpt_root))

    base = f"https://test.local/bundles/{TEST_MODEL_ID}"
    body = UploadAppRequest(
        bundle_urls=[
            f"{base}/app/config/config_fed_server.json",
            f"{base}/app/config/config_fed_client.json",
            f"{base}/app/custom/config.json",
            f"{base}/app/custom/evaluator.py",
            f"{base}/server_checkpoints/weights.pt",
        ],
        project_id="123456789",
        cohort_query="SELECT 1",
        trusts=["Trust_1", "Trust_2"],
    )

    response = upload_application(TEST_MODEL_ID, body, str(job_root))

    assert "Application uploaded successfully" in response["message"]
    # Checkpoint staged on the shared volume, keyed by model_id.
    assert (ckpt_root / TEST_MODEL_ID / "weights.pt").is_file()
    # And NOT anywhere under the FLARE job dir (so it is never shipped to clients).
    assert not list((job_root / TEST_MODEL_ID).rglob("weights.pt"))


@patch("fl_api.utils.upload.configure_environment", MagicMock())
@patch("fl_api.utils.upload.configure_meta", MagicMock())
@patch("fl_api.utils.upload.configure_server", MagicMock())
@patch("fl_api.utils.upload.configure_client", MagicMock())
@patch("fl_api.utils.upload.configure_config", MagicMock())
@patch("fl_api.utils.upload.validate_config", MagicMock())
@patch("fl_api.utils.upload.read_config", MagicMock(return_value={}))
@patch("fl_api.utils.upload.configure_flip_model_id", MagicMock())
def test_upload_clears_stale_server_checkpoints(
    mock_requests_get_success, mock_upload_correct_request, tmp_path, monkeypatch
):
    """A re-upload clears any previously-staged server checkpoints for the model, so a bundle that
    no longer ships a checkpoint cannot leave a stale one behind for the fl-server to load."""
    ckpt_root = tmp_path / "server-checkpoints"
    stale = ckpt_root / TEST_MODEL_ID / "stale-weights.pt"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"old")
    monkeypatch.setenv("SERVER_CHECKPOINT_ROOT", str(ckpt_root))

    response = upload_application(TEST_MODEL_ID, mock_upload_correct_request, str(tmp_path / "jobs"))

    assert "Application uploaded successfully" in response["message"]
    assert not stale.exists()


def test_upload_rejects_redirect_on_server_checkpoint_fetch(mock_requests_get_success, tmp_path, monkeypatch):
    """A 3xx on the staged-checkpoint stream is rejected like any other bundle fetch: the redirect
    target was never seen by validate_bundle_url (SSRF), so nothing must be written."""
    ckpt_root = tmp_path / "server-checkpoints"
    monkeypatch.setenv("SERVER_CHECKPOINT_ROOT", str(ckpt_root))
    mock_requests_get_success.return_value.is_redirect = True

    body = UploadAppRequest(
        bundle_urls=[f"https://test.local/bundles/{TEST_MODEL_ID}/server_checkpoints/weights.pt"],
        project_id="123456789",
        cohort_query="SELECT 1",
        trusts=["Trust_1"],
    )

    with pytest.raises(HTTPException) as exc:
        upload_application(TEST_MODEL_ID, body, str(tmp_path / "jobs"))
    assert exc.value.status_code == 400
    assert not (ckpt_root / TEST_MODEL_ID / "weights.pt").exists()


def test_validate_config_valid():
    valid_config = {
        "LOCAL_ROUNDS": 5,
        "GLOBAL_ROUNDS": 10,
        "IGNORE_RESULT_ERROR": True,
        "AGGREGATOR": "InTimeAccumulateWeightedAggregator",
        "AGGREGATION_WEIGHTS": {"client1": 1.0},
    }
    config = validate_config(valid_config)
    assert config.LOCAL_ROUNDS == 5
    assert config.GLOBAL_ROUNDS == 10
    assert config.IGNORE_RESULT_ERROR is True
    assert config.AGGREGATOR == "InTimeAccumulateWeightedAggregator"
    assert config.AGGREGATION_WEIGHTS == {"client1": 1.0}


def test_validate_config_invalid_type():
    with pytest.raises(ValueError, match="Provided config is not a valid dictionary"):
        validate_config("not-a-dict")


def test_validate_config_skips_invalid_rounds():
    config = {
        "LOCAL_ROUNDS": -1,
        "GLOBAL_ROUNDS": 0,
        "IGNORE_RESULT_ERROR": True,
        "AGGREGATOR": "InTimeAccumulateWeightedAggregator",
    }
    result = validate_config(config)
    assert result.LOCAL_ROUNDS is None
    assert result.GLOBAL_ROUNDS is None


def test_validate_config_invalid_aggregator():
    invalid_config = {
        "LOCAL_ROUNDS": 5,
        "GLOBAL_ROUNDS": 10,
        "AGGREGATOR": "invalid_agg",
    }
    with pytest.raises(ValueError, match="Unknown aggregator: invalid_agg"):
        validate_config(invalid_config)


def test_validate_config_accepts_aggregate_only_regex():
    config = validate_config({"AGGREGATE_ONLY_REGEX": "omni_heads"})
    assert config.AGGREGATE_ONLY_REGEX == "omni_heads"


def test_validate_config_rejects_invalid_aggregate_only_regex():
    with pytest.raises(ValueError, match="not a valid regex"):
        validate_config({"AGGREGATE_ONLY_REGEX": "([unclosed"})


def test_validate_config_rejects_non_string_aggregate_only_regex():
    with pytest.raises(ValueError, match="must be a string regex"):
        validate_config({"AGGREGATE_ONLY_REGEX": 123})


def test_validate_config_aggregate_only_regex_defaults_none():
    assert validate_config({"LOCAL_ROUNDS": 3}).AGGREGATE_ONLY_REGEX is None


def test_validate_config_accepts_best_model_metric():
    config = validate_config({"BEST_MODEL_METRIC": "VAL_DICE", "GLOBAL_ROUNDS": 3})
    assert config.BEST_MODEL_METRIC == "VAL_DICE"
    # Minimize defaults False (higher-is-better) when only the metric is given.
    assert config.BEST_MODEL_METRIC_MINIMIZE is False


def test_validate_config_best_model_metric_minimize():
    config = validate_config({"BEST_MODEL_METRIC": "VAL_LOSS", "BEST_MODEL_METRIC_MINIMIZE": True, "GLOBAL_ROUNDS": 3})
    assert config.BEST_MODEL_METRIC == "VAL_LOSS"
    assert config.BEST_MODEL_METRIC_MINIMIZE is True


def test_validate_config_strips_best_model_metric():
    # A stray space in the JSON must not survive into the stored label, where it would silently
    # never match the metric the trainer reports.
    config = validate_config({"BEST_MODEL_METRIC": " VAL-F1-SCORE ", "GLOBAL_ROUNDS": 3})
    assert config.BEST_MODEL_METRIC == "VAL-F1-SCORE"


def test_validate_config_rejects_best_model_metric_without_global_rounds():
    # GLOBAL_ROUNDS unset resolves to the platform default of 1, where the selector can never fire.
    with pytest.raises(ValueError, match="BEST_MODEL_METRIC requires GLOBAL_ROUNDS >= 2"):
        validate_config({"BEST_MODEL_METRIC": "VAL_DICE"})


def test_validate_config_rejects_best_model_metric_with_single_round():
    with pytest.raises(ValueError, match="BEST_MODEL_METRIC requires GLOBAL_ROUNDS >= 2"):
        validate_config({"BEST_MODEL_METRIC": "VAL_DICE", "GLOBAL_ROUNDS": 1})


def test_validate_config_rejects_best_model_metric_with_invalid_rounds():
    # An out-of-range GLOBAL_ROUNDS is silently dropped and also resolves to the 1-round default.
    with pytest.raises(ValueError, match="BEST_MODEL_METRIC requires GLOBAL_ROUNDS >= 2"):
        validate_config({"BEST_MODEL_METRIC": "VAL_DICE", "GLOBAL_ROUNDS": 0})


def test_validate_config_rejects_non_string_best_model_metric():
    with pytest.raises(ValueError, match="BEST_MODEL_METRIC must be a non-empty string"):
        validate_config({"BEST_MODEL_METRIC": 123})


def test_validate_config_rejects_empty_best_model_metric():
    with pytest.raises(ValueError, match="BEST_MODEL_METRIC must be a non-empty string"):
        validate_config({"BEST_MODEL_METRIC": ""})


def test_validate_config_rejects_whitespace_only_best_model_metric():
    with pytest.raises(ValueError, match="BEST_MODEL_METRIC must be a non-empty string"):
        validate_config({"BEST_MODEL_METRIC": "   "})


def test_validate_config_rejects_non_bool_best_model_metric_minimize():
    with pytest.raises(ValueError, match="BEST_MODEL_METRIC_MINIMIZE must be a boolean"):
        validate_config({"BEST_MODEL_METRIC": "VAL_DICE", "BEST_MODEL_METRIC_MINIMIZE": "yes"})


def test_validate_config_rejects_best_model_metric_minimize_without_metric():
    with pytest.raises(ValueError, match="BEST_MODEL_METRIC_MINIMIZE requires BEST_MODEL_METRIC"):
        validate_config({"BEST_MODEL_METRIC_MINIMIZE": True})


def test_validate_config_best_model_metric_defaults_none():
    result = validate_config({"LOCAL_ROUNDS": 3})
    assert result.BEST_MODEL_METRIC is None
    assert result.BEST_MODEL_METRIC_MINIMIZE is False


def test_validate_config_rejects_non_dict_weights():
    with pytest.raises(ValueError, match="AGGREGATION_WEIGHTS must be a dictionary"):
        validate_config({"AGGREGATION_WEIGHTS": ["client1"]})


def test_validate_config_invalid_weights():
    bad_weights = {
        "AGGREGATION_WEIGHTS": {"client1": "not-a-number"},
        "AGGREGATOR": "InTimeAccumulateWeightedAggregator",
    }
    with pytest.raises(ValueError, match="Invalid weight"):
        validate_config(bad_weights)


def test_upload_app_rejects_non_https_bundle_url():
    """Non-https bundle URLs (SSRF vector, e.g. the metadata endpoint) are rejected."""
    body = UploadAppRequest(
        bundle_urls=[f"http://169.254.169.254/bundles/{TEST_MODEL_ID}/app/custom/trainer.py"],
        project_id="123456789",
        cohort_query="SELECT 1",
        trusts=["Trust_1"],
    )
    with pytest.raises(HTTPException) as exc:
        upload_application(TEST_MODEL_ID, body, TMP_PATH_UPLOAD_DIR)
    assert exc.value.status_code == 400


def test_upload_app_rejects_disallowed_bundle_host(monkeypatch):
    """When BUNDLE_URL_ALLOWED_ORIGINS is set, off-origin bundle URLs are rejected."""
    monkeypatch.setenv("BUNDLE_URL_ALLOWED_ORIGINS", "https://objectstore.internal")
    body = UploadAppRequest(
        bundle_urls=[f"https://test.local/bundles/{TEST_MODEL_ID}/app/custom/trainer.py"],
        project_id="123456789",
        cohort_query="SELECT 1",
        trusts=["Trust_1"],
    )
    with pytest.raises(HTTPException) as exc:
        upload_application(TEST_MODEL_ID, body, TMP_PATH_UPLOAD_DIR)
    assert exc.value.status_code == 400


def test_upload_app_rejects_redirect_response(mock_requests_get_success, mock_upload_correct_request):
    """A 3xx redirect on a bundle fetch is rejected: it would dodge validate_bundle_url (SSRF)."""
    mock_requests_get_success.return_value.is_redirect = True

    with pytest.raises(HTTPException) as exc:
        upload_application(TEST_MODEL_ID, mock_upload_correct_request, TMP_PATH_UPLOAD_DIR)
    assert exc.value.status_code == 400


def test_upload_app_raises_on_http_error_response(mock_requests_get_success, mock_upload_correct_request):
    """A non-2xx bundle response raises, so its error body is never written as the bundle file."""
    mock_requests_get_success.return_value.raise_for_status.side_effect = requests.HTTPError("404 Not Found")

    with pytest.raises(requests.HTTPError):
        upload_application(TEST_MODEL_ID, mock_upload_correct_request, TMP_PATH_UPLOAD_DIR)


# ── the job's meta.json and the model id (FLIP#70) ──────────────────────────────────


def _assemble(tmp_path, monkeypatch, meta=None, resources=None, default_gpus=0, app="app"):
    """Run upload_application end to end on real files: a minimal app, plus the job's meta.json if given."""
    import fl_api.utils.upload as upload_module

    base = f"https://test.local/bundles/{TEST_MODEL_ID}"
    files = {
        f"{app}/config/config_fed_server.json": {"workflows": [], "components": []},
        f"{app}/config/config_fed_client.json": {"executors": []},
        f"{app}/custom/config.json": {"LOCAL_ROUNDS": 1, "GLOBAL_ROUNDS": 2, "FLIP_MODEL_ID": "stale"},
    }
    if meta is not None:
        files["meta.json"] = meta

    def fake_get(url, **_):
        resp = MagicMock(is_redirect=False, is_permanent_redirect=False)
        content = files[url.removeprefix(f"{base}/")]
        resp.content = (content if isinstance(content, str) else json.dumps(content)).encode()
        return resp

    monkeypatch.setattr(upload_module.requests, "get", fake_get)
    monkeypatch.setenv("SERVER_CHECKPOINT_ROOT", str(tmp_path / "server-checkpoints"))
    monkeypatch.setattr(
        "fl_api.utils.job_resources.get_settings", lambda: MagicMock(JOB_RESOURCE_SPEC_NUM_GPUS=default_gpus)
    )
    body = UploadAppRequest(
        bundle_urls=[f"{base}/{path}" for path in files],
        project_id="123456789",
        cohort_query="SELECT 1",
        trusts=["Trust_1", "Trust_2"],
        resources=resources,
    )
    response = upload_application(TEST_MODEL_ID, body, str(tmp_path / "jobs"))
    job_dir = tmp_path / "jobs" / TEST_MODEL_ID
    return response, json.loads((job_dir / "meta.json").read_text()), job_dir


def test_a_researchers_recipe_export_keeps_its_request_and_flip_writes_the_runs_sites(tmp_path, monkeypatch):
    exported = {
        "name": "fedavg",
        "resource_spec": {"@default": {"num_of_gpus": 1, "memory": "16Gi"}},
        "min_clients": 1,
        "deploy_map": {"app": ["@ALL"]},
        "custom_props": {"model_id": "00000000-0000-0000-0000-000000000001", "team": "radiology"},
    }

    response, meta, _ = _assemble(tmp_path, monkeypatch, meta=exported)

    assert meta == {
        "name": TEST_MODEL_ID,
        "resource_spec": {"@default": {"num_of_gpus": 1, "memory": "16Gi"}},
        "deploy_map": {"app": ["server", "Trust_1", "Trust_2"]},
        "min_clients": 2,
        "mandatory_clients": ["Trust_1", "Trust_2"],
        "custom_props": {"team": "radiology"},
    }
    assert response["resources"] == {"num_gpus": 1}
    assert response["resources_source"] == "meta.json"


def test_the_model_id_goes_in_the_apps_config_json_not_meta_json(tmp_path, monkeypatch):
    """Where FLIP's server components read it, as Flower's flip-model-id lives in config.toml."""
    _, meta, job_dir = _assemble(tmp_path, monkeypatch, meta={"resource_spec": {}})

    config = json.loads((job_dir / "app" / "custom" / "config.json").read_text())
    assert config["FLIP_MODEL_ID"] == TEST_MODEL_ID
    assert "custom_props" not in meta


def test_without_a_meta_json_the_default_applies(tmp_path, monkeypatch):
    response, meta, _ = _assemble(tmp_path, monkeypatch, default_gpus=1)

    assert meta["resource_spec"] == {"@default": {"num_of_gpus": 1}}
    assert response["resources_source"] == "default"


def test_a_templates_empty_resource_spec_leaves_the_default_in_force(tmp_path, monkeypatch):
    response, meta, _ = _assemble(tmp_path, monkeypatch, meta={"resource_spec": {}}, default_gpus=1)

    assert meta["resource_spec"] == {"@default": {"num_of_gpus": 1}}
    assert response["resources_source"] == "default"


def test_a_submission_override_beats_the_jobs_meta_json(tmp_path, monkeypatch):
    response, meta, _ = _assemble(
        tmp_path,
        monkeypatch,
        meta={"resource_spec": {"@default": {"num_of_gpus": 1, "num_of_cpus": 4}}},
        resources=JobResources(num_gpus=0),
    )

    assert meta["resource_spec"] == {"@default": {"num_of_cpus": 4}}
    assert response["resources"] == {"num_gpus": 0}
    assert response["resources_source"] == "submission"


def test_a_meta_json_that_is_not_json_is_refused_naming_the_file(tmp_path, monkeypatch):
    with pytest.raises(ResearcherRefusal, match="meta.json is not valid JSON"):
        _assemble(tmp_path, monkeypatch, meta='{"resource_spec": ')


def test_a_meta_json_choosing_how_a_trust_runs_the_job_is_refused(tmp_path, monkeypatch):
    with pytest.raises(ResearcherRefusal, match="launcher_spec"):
        _assemble(tmp_path, monkeypatch, meta={"launcher_spec": {"default": {"k8s": {"image": "x"}}}})


def test_a_job_of_per_site_variants_alone_is_refused(tmp_path, monkeypatch):
    """FLIP deploys app/ to every selected trust. The hub always ships meta.json, which is not an app folder."""
    with pytest.raises(ResearcherRefusal, match="no app/ folder"):
        _assemble(tmp_path, monkeypatch, meta={"resource_spec": {}}, app="app-trust1")


def test_a_run_override_still_uploads_when_the_default_is_misconfigured(tmp_path, monkeypatch):
    """The default is read only when nothing else sets the count, so a bad one cannot block other jobs."""
    response, _, _ = _assemble(tmp_path, monkeypatch, resources=JobResources(num_gpus=1), default_gpus=99)

    assert response["resources_source"] == "submission"
