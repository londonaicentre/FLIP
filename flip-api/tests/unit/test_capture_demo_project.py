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
"""The demo capture script writes one fixture per endpoint the demo server serves (FLIP#1746)."""

import json
from pathlib import Path
from typing import Any

import pytest

from tests.capture_demo_project import capture, main, parse_args

PROJECT_ID = "11111111-1111-4111-8111-111111111111"
QUERY_ID = "22222222-2222-4222-8222-222222222222"
MODELS = ["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"]
TRUSTS = [
    {"id": "t-1", "name": "Guy's and St Thomas' Trust", "code": "GSTT"},
    {"id": "t-2", "name": "King's College Hospital", "code": "KCH"},
]


class FakeResponse:
    def __init__(self, status_code: int, payload: Any) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self) -> Any:
        return self._payload


class FakeSession:
    """Answers the paths the capture asks for, in the shapes the hub returns."""

    def __init__(self, *, image_status: int = 200) -> None:
        self.image_status = image_status
        self.calls: list[str] = []

    def _answer(self, path: str) -> FakeResponse:
        self.calls.append(path)
        if path.startswith("https://store.example/"):
            return FakeResponse(200, {"job_type": "standard"})
        if path.endswith(f"/projects/{PROJECT_ID}"):
            return FakeResponse(200, {"id": PROJECT_ID, "approvedTrusts": TRUSTS, "query": {"id": QUERY_ID}})
        if path.endswith(f"/projects/{PROJECT_ID}/models"):
            return FakeResponse(200, {"data": [{"id": m} for m in MODELS]})
        if path.endswith(f"/projects/{PROJECT_ID}/cohort-snapshots"):
            return FakeResponse(200, [{"trustId": "t-1", "trustName": TRUSTS[0]["name"], "status": "frozen"}])
        if path.endswith(f"/projects/{PROJECT_ID}/image/status"):
            return FakeResponse(self.image_status, [{"trustId": "t-1", "trustName": TRUSTS[0]["name"]}])
        if path.endswith(f"/cohort/{QUERY_ID}"):
            counts = {"t-1": 1, "t-2": 1, "t-cohort-only": 4}
            return FakeResponse(200, {"recordCount": 6, "trustsResults": [], "trustRecordCounts": counts})
        for model in MODELS:
            if f"/step/model/{model}" in path:
                return FakeResponse(200, {"modelId": model, "trusts": TRUSTS})
            if path.endswith(f"/files/model/{model}/config.json"):
                # The hub answers with a presigned URL; the file body lives behind it.
                return FakeResponse(
                    200, {"fileName": "config.json", "url": f"https://store.example/{model}/config.json"}
                )
            if path.endswith(f"/model/{model}/metrics"):
                return FakeResponse(200, [])
            if path.endswith(f"/model/{model}/logs"):
                return FakeResponse(200, [{"log": "Model Queued (1)"}])
            if path.endswith(f"/files/model/{model}/get/files"):
                return FakeResponse(200, [{"name": "model.py"}])
        return FakeResponse(404, {"detail": f"unexpected {path}"})

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        if url.startswith("https://store.example/"):
            # A presigned URL carries its own auth: the hub's bearer token must not ride along.
            assert not kwargs.get("headers"), "hub headers sent to the object store"
        return self._answer(url)

    def post(self, url: str, **_: Any) -> FakeResponse:
        return self._answer(url)


def test_capture_writes_one_file_per_endpoint_and_per_model(tmp_path: Path) -> None:
    written = capture(FakeSession(), {}, PROJECT_ID, tmp_path)  # type: ignore[arg-type]

    names = sorted(p.name for p in written)
    per_model = ["step", "config", "metrics", "logs", "files"]
    expected = ["project.json", "models.json", "cohort.json", "cohort_snapshots.json", "image_status.json"]
    expected += [f"m{n}_{kind}.json" for n in (1, 2) for kind in per_model]
    assert names == sorted(expected)
    assert json.loads((tmp_path / "m2_step.json").read_text())["modelId"] == MODELS[1]
    assert json.loads((tmp_path / "m1_config.json").read_text()) == {"job_type": "standard"}


def test_capture_stores_the_config_body_not_its_presigned_url(tmp_path: Path) -> None:
    capture(FakeSession(), {"authorization": "Bearer t"}, PROJECT_ID, tmp_path)  # type: ignore[arg-type]

    for n in (1, 2):
        body = (tmp_path / f"m{n}_config.json").read_text()
        assert "store.example" not in body
        assert json.loads(body) == {"job_type": "standard"}


def test_capture_skips_image_status_on_404(tmp_path: Path) -> None:
    written = capture(FakeSession(image_status=404), {}, PROJECT_ID, tmp_path)  # type: ignore[arg-type]

    assert "image_status.json" not in {p.name for p in written}
    assert len(written) == 4 + 2 * 5


def test_capture_raises_on_any_other_failure(tmp_path: Path) -> None:
    session = FakeSession()
    session.image_status = 500

    with pytest.raises(RuntimeError, match="image/status"):
        capture(session, {}, PROJECT_ID, tmp_path)  # type: ignore[arg-type]


def test_capture_prints_distinct_trusts(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    capture(FakeSession(), {}, PROJECT_ID, tmp_path)  # type: ignore[arg-type]

    out = capsys.readouterr().out
    for trust in TRUSTS:
        assert out.count(f"{trust['id']}  {trust['code']}  {trust['name']}") == 1


def test_capture_lists_trusts_named_only_by_id(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    capture(FakeSession(), {}, PROJECT_ID, tmp_path)  # type: ignore[arg-type]

    lines = capsys.readouterr().out.splitlines()
    assert sum(line.split()[0] == "t-cohort-only" for line in lines if line.startswith("  ")) == 1
    assert sum(line.split()[0] == "t-1" for line in lines if line.startswith("  ")) == 1


def test_parse_args_requires_project_id_and_slug() -> None:
    with pytest.raises(SystemExit):
        parse_args(["--slug", "ehr-risk"])
    with pytest.raises(SystemExit):
        parse_args(["--project-id", PROJECT_ID])

    args = parse_args(["--project-id", PROJECT_ID, "--slug", "ehr-risk"])
    assert args.out_root == Path("../flip-ui/mocks/demo/projects")


def test_main_writes_under_out_root_slug_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import tests.capture_demo_project as module

    monkeypatch.setattr(module, "authenticate", lambda: {})
    monkeypatch.setattr(module.requests, "Session", lambda: FakeSession())

    assert main(["--project-id", PROJECT_ID, "--slug", "ehr-risk", "--out-root", str(tmp_path)]) == 0
    assert (tmp_path / "ehr-risk" / "data" / "project.json").exists()
