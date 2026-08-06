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

import pytest

from flip.nvflare.site_policy import (
    PERCENTILE_FILTER_PATH,
    SCOPE_NAME,
    SitePolicyError,
    build_policy_json,
    main,
    parse_env,
    render,
)


class TestParseEnv:
    """parse_env turns FL_SITE_PRIVACY_* env vars into a validated SitePolicy (or None)."""

    def test_no_policy_returns_none(self):
        assert parse_env({}) is None

    @pytest.mark.parametrize("value", ["", "   "])
    def test_empty_or_whitespace_policy_is_unset(self, value):
        # Commented-out example files and compose `${VAR:-}` defaults surface as "" — must equal unset.
        assert parse_env({"FL_SITE_PRIVACY_POLICY": value}) is None

    def test_percentile_defaults(self):
        policy = parse_env({"FL_SITE_PRIVACY_POLICY": "percentile"})
        assert policy is not None
        assert policy.percentile == 10
        assert policy.gamma == 0.01

    def test_percentile_explicit_params(self):
        policy = parse_env(
            {
                "FL_SITE_PRIVACY_POLICY": "percentile",
                "FL_SITE_PRIVACY_PERCENTILE": "25",
                "FL_SITE_PRIVACY_GAMMA": "0.05",
            }
        )
        assert policy is not None
        assert policy.percentile == 25
        assert policy.gamma == 0.05

    def test_policy_name_is_case_insensitive(self):
        policy = parse_env({"FL_SITE_PRIVACY_POLICY": "Percentile"})
        assert policy is not None
        assert policy.percentile == 10

    def test_empty_param_value_uses_default(self):
        policy = parse_env({"FL_SITE_PRIVACY_POLICY": "percentile", "FL_SITE_PRIVACY_PERCENTILE": ""})
        assert policy is not None
        assert policy.percentile == 10

    @pytest.mark.parametrize("value", ["-1", "101", "abc", "inf", "nan"])
    def test_percentile_out_of_bounds_rejected(self, value):
        # Stock PercentilePrivacy silently no-ops (fails OPEN) outside [0, 100] — must error at render time.
        with pytest.raises(SitePolicyError, match="FL_SITE_PRIVACY_PERCENTILE"):
            parse_env({"FL_SITE_PRIVACY_POLICY": "percentile", "FL_SITE_PRIVACY_PERCENTILE": value})

    @pytest.mark.parametrize("value", ["0", "-0.1", "x", "inf", "nan"])
    def test_gamma_out_of_bounds_rejected(self, value):
        # Stock PercentilePrivacy silently no-ops (fails OPEN) when gamma <= 0 — must error at render time.
        with pytest.raises(SitePolicyError, match="FL_SITE_PRIVACY_GAMMA"):
            parse_env({"FL_SITE_PRIVACY_POLICY": "percentile", "FL_SITE_PRIVACY_GAMMA": value})

    def test_unknown_policy_rejected(self):
        with pytest.raises(SitePolicyError, match="FL_SITE_PRIVACY_POLICY"):
            parse_env({"FL_SITE_PRIVACY_POLICY": "laplace"})

    def test_params_without_policy_rejected(self):
        # Params set but no policy selected — refusing to guess beats silently running unprotected.
        with pytest.raises(SitePolicyError, match="FL_SITE_PRIVACY_POLICY"):
            parse_env({"FL_SITE_PRIVACY_PERCENTILE": "10"})


class TestBuildPolicyJson:
    """The rendered document must match NVFLARE's privacy.json schema: one scope, always a default_scope."""

    def test_percentile_document_shape(self):
        policy = parse_env({"FL_SITE_PRIVACY_POLICY": "percentile"})
        doc = build_policy_json(policy)

        assert doc["default_scope"] == SCOPE_NAME
        assert len(doc["scopes"]) == 1
        scope = doc["scopes"][0]
        assert scope["name"] == SCOPE_NAME
        assert "task_data_filters" not in scope
        (filter_entry,) = scope["task_result_filters"]
        assert filter_entry["path"] == PERCENTILE_FILTER_PATH
        assert filter_entry["args"] == {"percentile": 10, "gamma": 0.01}
        # Client-side result filters default to direction "out"; omitting the key keeps stock semantics.
        assert "direction" not in filter_entry

    def test_integral_percentile_serialises_as_int(self):
        policy = parse_env({"FL_SITE_PRIVACY_POLICY": "percentile", "FL_SITE_PRIVACY_PERCENTILE": "25"})
        assert json.loads(json.dumps(build_policy_json(policy)))["scopes"][0]["task_result_filters"][0]["args"][
            "percentile"
        ] == 25


class TestRender:
    def test_absent_when_no_policy(self, tmp_path):
        out_path = tmp_path / "privacy.json"

        assert render({}, out_path) == "absent"
        assert not out_path.exists()

    def test_removes_stale_file_when_unset(self, tmp_path):
        out_path = tmp_path / "privacy.json"
        out_path.write_text("{}")
        sample = tmp_path / "privacy.json.sample"
        sample.write_text("sample untouched")

        assert render({}, out_path) == "removed"
        assert not out_path.exists()
        assert sample.read_text() == "sample untouched"

    def test_writes_policy_file(self, tmp_path):
        out_path = tmp_path / "privacy.json"

        assert render({"FL_SITE_PRIVACY_POLICY": "percentile"}, out_path) == "written"
        doc = json.loads(out_path.read_text())
        assert doc["default_scope"] == SCOPE_NAME

    def test_overwrites_stale_content(self, tmp_path):
        out_path = tmp_path / "privacy.json"
        out_path.write_text('{"scopes": [], "default_scope": "old"}')

        assert render(
            {"FL_SITE_PRIVACY_POLICY": "percentile", "FL_SITE_PRIVACY_PERCENTILE": "20"}, out_path
        ) == "written"
        doc = json.loads(out_path.read_text())
        assert doc["scopes"][0]["task_result_filters"][0]["args"]["percentile"] == 20

    def test_check_only_never_writes(self, tmp_path):
        out_path = tmp_path / "privacy.json"

        assert render({"FL_SITE_PRIVACY_POLICY": "percentile"}, out_path, check_only=True) == "written"
        assert not out_path.exists()

    def test_check_only_never_removes(self, tmp_path):
        out_path = tmp_path / "privacy.json"
        out_path.write_text("{}")

        assert render({}, out_path, check_only=True) == "removed"
        assert out_path.exists()


class TestMain:
    def test_writes_and_exits_zero(self, tmp_path, capsys):
        out_path = tmp_path / "privacy.json"

        exit_code = main([str(out_path)], env={"FL_SITE_PRIVACY_POLICY": "percentile"})

        assert exit_code == 0
        assert out_path.exists()
        assert "ACTIVE" in capsys.readouterr().out

    def test_no_policy_exits_zero(self, tmp_path, capsys):
        out_path = tmp_path / "privacy.json"

        exit_code = main([str(out_path)], env={})

        assert exit_code == 0
        assert not out_path.exists()
        assert "no site privacy policy" in capsys.readouterr().out

    def test_invalid_config_exits_one_and_names_variable(self, tmp_path, capsys):
        out_path = tmp_path / "privacy.json"

        exit_code = main([str(out_path)], env={"FL_SITE_PRIVACY_POLICY": "bogus"})

        assert exit_code == 1
        assert not out_path.exists()
        err = capsys.readouterr().err
        assert "[site-privacy] FATAL" in err
        assert "FL_SITE_PRIVACY_POLICY" in err
        assert "bogus" in err

    def test_check_flag_validates_without_writing(self, tmp_path):
        out_path = tmp_path / "privacy.json"

        assert main(["--check", str(out_path)], env={"FL_SITE_PRIVACY_POLICY": "percentile"}) == 0
        assert not out_path.exists()
        assert main(["--check", str(out_path)], env={"FL_SITE_PRIVACY_POLICY": "bogus"}) == 1


class TestNvflareRoundTrip:
    """Upgrade canary: NVFLARE's own (private-API) site-policy loader must accept our rendered file.

    Deliberately exercises ``nvflare.private`` internals so that an NVFLARE upgrade that changes the
    privacy.json contract fails here, in unit tests, rather than at job time on a trust.
    """

    def test_rendered_policy_loads_via_create_privacy_manager(self, tmp_path):
        from nvflare.apis.fl_constant import FilterKey
        from nvflare.apis.workspace import Workspace
        from nvflare.private.fed.app.fl_conf import create_privacy_manager

        (tmp_path / "local").mkdir()
        (tmp_path / "startup").mkdir()
        render({"FL_SITE_PRIVACY_POLICY": "percentile"}, tmp_path / "local" / "privacy.json")

        workspace = Workspace(root_dir=str(tmp_path), site_name="site-test")
        manager = create_privacy_manager(workspace, names_only=False)

        assert manager.is_policy_defined()
        scope = manager.get_scope("")  # empty scope name resolves to default_scope, as FLIP jobs never set one
        assert scope is not None
        assert scope.name == SCOPE_NAME
        (result_filter,) = scope.task_result_filters[FilterKey.OUT]
        assert type(result_filter).__module__ + "." + type(result_filter).__name__ == PERCENTILE_FILTER_PATH
        assert scope.task_data_filters[FilterKey.IN] == []
        assert scope.task_data_filters[FilterKey.OUT] == []
