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
import runpy
import sys

import pytest

from flip.nvflare.site_policy import (
    PERCENTILE_FILTER_PATH,
    SCOPE_NAME,
    SitePolicy,
    SitePolicyError,
    build_policy_json,
    main,
    parse_env,
    parse_governance_file,
    render,
    resolve_policy,
)


def _write(tmp_path, text: str) -> str:
    """Write a governance document and return its path as a string."""
    path = tmp_path / "governance.toml"
    path.write_text(text, encoding="utf-8")
    return str(path)


class TestGovernanceDocument:
    """The [fl_privacy] section of the shared trust governance document (FLIP#1259).

    Same strictness as the env-var path: the stock PercentilePrivacy filter fails OPEN on
    out-of-range parameters, so anything unrecognised must stop the container instead of
    leaving a weaker filter silently in force.
    """

    def test_section_is_parsed(self, tmp_path):
        path = _write(
            tmp_path,
            """
            [fl_privacy]
            policy = "percentile"
            percentile = 25
            gamma = 0.5
            """,
        )

        assert parse_governance_file(path) == SitePolicy(percentile=25, gamma=0.5)

    def test_parameters_default_when_omitted(self, tmp_path):
        path = _write(tmp_path, '[fl_privacy]\npolicy = "percentile"\n')

        assert parse_governance_file(path) == SitePolicy(percentile=10, gamma=0.01)

    def test_absent_section_is_none(self, tmp_path):
        """A trust may use the document for disclosure rules only."""
        path = _write(tmp_path, "[disclosure]\nmin_cohort_size = 20\n")

        assert parse_governance_file(path) is None

    def test_other_planes_sections_are_tolerated(self, tmp_path):
        """[disclosure]/[access] belong to data-access-api and must not be rejected here."""
        path = _write(
            tmp_path,
            """
            [disclosure]
            min_cohort_size = 20

            [[access.rule]]
            id = "r1"
            action = "cohort.dataframe"
            effect = "deny"

            [fl_privacy]
            policy = "percentile"
            """,
        )

        assert parse_governance_file(path) == SitePolicy(percentile=10, gamma=0.01)

    def test_unknown_section_is_rejected(self, tmp_path):
        path = _write(tmp_path, "[disclosre]\nmin_cohort_size = 20\n")

        with pytest.raises(SitePolicyError, match="unrecognised section"):
            parse_governance_file(path)

    def test_unknown_fl_privacy_key_is_rejected(self, tmp_path):
        """A typo'd parameter would leave the weaker default in force — reject it."""
        path = _write(tmp_path, '[fl_privacy]\npolicy = "percentile"\npercentil = 25\n')

        with pytest.raises(SitePolicyError, match="unrecognised key"):
            parse_governance_file(path)

    def test_parameters_without_policy_are_rejected(self, tmp_path):
        path = _write(tmp_path, "[fl_privacy]\npercentile = 25\n")

        with pytest.raises(SitePolicyError, match="no 'policy'"):
            parse_governance_file(path)

    def test_unknown_policy_is_rejected(self, tmp_path):
        path = _write(tmp_path, '[fl_privacy]\npolicy = "laplace"\n')

        with pytest.raises(SitePolicyError, match="not a known policy"):
            parse_governance_file(path)

    @pytest.mark.parametrize("gamma", [0, -1])
    def test_fail_open_gamma_is_rejected(self, tmp_path, gamma):
        """gamma <= 0 makes stock PercentilePrivacy forward the update unfiltered."""
        path = _write(tmp_path, f'[fl_privacy]\npolicy = "percentile"\ngamma = {gamma}\n')

        with pytest.raises(SitePolicyError, match="out of bounds"):
            parse_governance_file(path)

    @pytest.mark.parametrize("percentile", [-1, 101])
    def test_out_of_range_percentile_is_rejected(self, tmp_path, percentile):
        path = _write(tmp_path, f'[fl_privacy]\npolicy = "percentile"\npercentile = {percentile}\n')

        with pytest.raises(SitePolicyError, match="out of bounds"):
            parse_governance_file(path)

    def test_malformed_toml_is_rejected(self, tmp_path):
        path = _write(tmp_path, "[fl_privacy\npolicy = 'percentile'\n")

        with pytest.raises(SitePolicyError, match="not valid TOML"):
            parse_governance_file(path)

    def test_unreadable_file_is_rejected(self, tmp_path):
        """Configured-but-missing must fail closed, not fall back to the env vars."""
        with pytest.raises(SitePolicyError, match="could not be read"):
            parse_governance_file(str(tmp_path / "nope.toml"))

    def test_shipped_example_parses(self):
        """The worked example operators copy must actually load."""
        from pathlib import Path

        example = Path(__file__).resolve().parents[4] / "trust" / "governance.example.toml"
        assert example.is_file(), f"expected the shipped example at {example}"

        assert parse_governance_file(str(example)) == SitePolicy(percentile=10, gamma=0.01)


class TestResolvePolicy:
    """Precedence between the governance document and FL_SITE_PRIVACY_* (FLIP#1259)."""

    def test_env_only(self):
        policy, source = resolve_policy({"FL_SITE_PRIVACY_POLICY": "percentile"})

        assert policy == SitePolicy(percentile=10, gamma=0.01)
        assert "env" in source

    def test_document_only(self, tmp_path):
        path = _write(tmp_path, '[fl_privacy]\npolicy = "percentile"\npercentile = 30\n')

        policy, source = resolve_policy({"ACCESS_POLICY_FILE": path})

        assert policy == SitePolicy(percentile=30, gamma=0.01)
        assert "ACCESS_POLICY_FILE" in source

    def test_document_wins_over_env(self, tmp_path, capsys):
        """Document wins, and says so — a silent override would be the dangerous case."""
        path = _write(tmp_path, '[fl_privacy]\npolicy = "percentile"\npercentile = 30\n')

        policy, source = resolve_policy(
            {"ACCESS_POLICY_FILE": path, "FL_SITE_PRIVACY_POLICY": "percentile", "FL_SITE_PRIVACY_PERCENTILE": "5"}
        )

        assert policy == SitePolicy(percentile=30, gamma=0.01)
        assert "ACCESS_POLICY_FILE" in source
        assert "IGNORING the FL_SITE_PRIVACY_*" in capsys.readouterr().err

    def test_document_without_fl_privacy_falls_back_to_env(self, tmp_path):
        """A disclosure-only document leaves the env vars in charge of this filter."""
        path = _write(tmp_path, "[disclosure]\nmin_cohort_size = 20\n")

        policy, source = resolve_policy({"ACCESS_POLICY_FILE": path, "FL_SITE_PRIVACY_POLICY": "percentile"})

        assert policy == SitePolicy(percentile=10, gamma=0.01)
        assert "env" in source

    def test_neither_source_configured(self):
        assert resolve_policy({})[0] is None

    def test_invalid_env_is_still_rejected_when_a_document_wins(self, tmp_path):
        """A broken env var must not hide behind a valid document.

        Otherwise removing the document later would surprise the operator with a service
        that no longer starts, for a fault introduced long before.
        """
        path = _write(tmp_path, '[fl_privacy]\npolicy = "percentile"\n')

        with pytest.raises(SitePolicyError, match="unrecognised site privacy variable"):
            resolve_policy({"ACCESS_POLICY_FILE": path, "FL_SITE_PRIVACY_PERCENTIL": "5"})

    def test_precedence_warning_is_emitted_once_per_run(self, tmp_path, capsys):
        """main() must not re-resolve the policy — that printed the warning twice.

        render() returns the resolved policy so the operator sees one warning per start,
        not one per internal caller.
        """
        path = _write(tmp_path, '[fl_privacy]\npolicy = "percentile"\n')
        out_path = tmp_path / "privacy.json"

        rc = main(
            [str(out_path)],
            env={"ACCESS_POLICY_FILE": path, "FL_SITE_PRIVACY_POLICY": "percentile"},
        )

        assert rc == 0
        assert capsys.readouterr().err.count("IGNORING the FL_SITE_PRIVACY_*") == 1

    def test_render_uses_the_document(self, tmp_path):
        """End to end: a document-configured policy reaches the rendered privacy.json."""
        path = _write(tmp_path, '[fl_privacy]\npolicy = "percentile"\npercentile = 42\ngamma = 0.25\n')
        out_path = tmp_path / "privacy.json"

        assert render({"ACCESS_POLICY_FILE": path}, out_path)[0] == "written"

        document = json.loads(out_path.read_text())
        (filter_entry,) = document["scopes"][0]["task_result_filters"]
        assert filter_entry["args"] == {"percentile": 42, "gamma": 0.25}


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

    def test_typo_with_policy_set_rejected(self):
        # The dangerous case: the policy IS selected, so the typo'd name would otherwise be ignored and
        # the site would quietly run percentile=10 instead of the intended 25.
        with pytest.raises(SitePolicyError, match="FL_SITE_PRIVACY_PERCENTIL\\b"):
            parse_env({"FL_SITE_PRIVACY_POLICY": "percentile", "FL_SITE_PRIVACY_PERCENTIL": "25"})

    def test_typo_without_policy_rejected(self):
        with pytest.raises(SitePolicyError, match="FL_SITE_PRIVACY_GAMA"):
            parse_env({"FL_SITE_PRIVACY_GAMA": "0.05"})

    def test_multiple_unknown_vars_all_named(self):
        with pytest.raises(SitePolicyError) as excinfo:
            parse_env({"FL_SITE_PRIVACY_POLICY": "percentile", "FL_SITE_PRIVACY_ZZZ": "1", "FL_SITE_PRIVACY_AAA": "2"})
        # Sorted so the message is stable regardless of the environment's iteration order.
        assert "FL_SITE_PRIVACY_AAA, FL_SITE_PRIVACY_ZZZ" in str(excinfo.value)

    def test_empty_unknown_var_is_unset(self):
        # `${VAR:-}` interpolation surfaces as "" — an unset unknown name must not stop the client.
        policy = parse_env({"FL_SITE_PRIVACY_POLICY": "percentile", "FL_SITE_PRIVACY_PERCENTIL": "  "})
        assert policy is not None
        assert policy.percentile == 10

    def test_unrelated_env_vars_ignored(self):
        # os.environ is passed wholesale in production — only the FL_SITE_PRIVACY_ prefix is policed.
        assert parse_env({"PATH": "/usr/bin", "FL_BACKEND": "nvflare"}) is None


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
        assert (
            json.loads(json.dumps(build_policy_json(policy)))["scopes"][0]["task_result_filters"][0]["args"][
                "percentile"
            ]
            == 25
        )


class TestRender:
    def test_absent_when_no_policy(self, tmp_path):
        out_path = tmp_path / "privacy.json"

        assert render({}, out_path)[0] == "absent"
        assert not out_path.exists()

    def test_removes_stale_file_when_unset(self, tmp_path):
        out_path = tmp_path / "privacy.json"
        out_path.write_text("{}")
        sample = tmp_path / "privacy.json.sample"
        sample.write_text("sample untouched")

        assert render({}, out_path)[0] == "removed"
        assert not out_path.exists()
        assert sample.read_text() == "sample untouched"

    def test_writes_policy_file(self, tmp_path):
        out_path = tmp_path / "privacy.json"

        assert render({"FL_SITE_PRIVACY_POLICY": "percentile"}, out_path)[0] == "written"
        doc = json.loads(out_path.read_text())
        assert doc["default_scope"] == SCOPE_NAME

    def test_overwrites_stale_content(self, tmp_path):
        out_path = tmp_path / "privacy.json"
        out_path.write_text('{"scopes": [], "default_scope": "old"}')

        assert (
            render({"FL_SITE_PRIVACY_POLICY": "percentile", "FL_SITE_PRIVACY_PERCENTILE": "20"}, out_path)[0]
            == "written"
        )
        doc = json.loads(out_path.read_text())
        assert doc["scopes"][0]["task_result_filters"][0]["args"]["percentile"] == 20

    def test_check_only_never_writes(self, tmp_path):
        out_path = tmp_path / "privacy.json"

        assert render({"FL_SITE_PRIVACY_POLICY": "percentile"}, out_path, check_only=True)[0] == "written"
        assert not out_path.exists()

    def test_check_only_never_removes(self, tmp_path):
        out_path = tmp_path / "privacy.json"
        out_path.write_text("{}")

        assert render({}, out_path, check_only=True)[0] == "removed"
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

    def test_removes_stale_file_and_reports(self, tmp_path, capsys):
        out_path = tmp_path / "privacy.json"
        out_path.write_text("{}")

        exit_code = main([str(out_path)], env={})

        assert exit_code == 0
        assert not out_path.exists()
        assert "REMOVED" in capsys.readouterr().out

    def test_check_flag_reports_removal_without_removing(self, tmp_path, capsys):
        out_path = tmp_path / "privacy.json"
        out_path.write_text("{}")

        exit_code = main(["--check", str(out_path)], env={})

        assert exit_code == 0
        assert out_path.exists()
        assert "would remove (--check)" in capsys.readouterr().out

    def test_env_arg_omitted_reads_os_environ(self, tmp_path, monkeypatch):
        # The real CLI passes no env mapping — main must fall back to os.environ.
        out_path = tmp_path / "privacy.json"
        for var in ("FL_SITE_PRIVACY_PERCENTILE", "FL_SITE_PRIVACY_GAMMA"):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("FL_SITE_PRIVACY_POLICY", "percentile")

        assert main([str(out_path)]) == 0
        assert json.loads(out_path.read_text())["default_scope"] == SCOPE_NAME


class TestModuleEntryPoint:
    """The fl-client entrypoint invokes ``python -m flip.nvflare.site_policy`` — the module must run as a script."""

    def test_runs_as_main_and_exits_via_systemexit(self, tmp_path, monkeypatch):
        out_path = tmp_path / "privacy.json"
        for var in ("FL_SITE_PRIVACY_PERCENTILE", "FL_SITE_PRIVACY_GAMMA"):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("FL_SITE_PRIVACY_POLICY", "percentile")
        monkeypatch.setattr(sys, "argv", ["site_policy", str(out_path)])
        # Drop the already-imported module so runpy re-executes it cleanly (no double-import warning).
        monkeypatch.delitem(sys.modules, "flip.nvflare.site_policy", raising=False)

        with pytest.raises(SystemExit) as excinfo:
            runpy.run_module("flip.nvflare.site_policy", run_name="__main__")

        assert excinfo.value.code == 0
        assert json.loads(out_path.read_text())["default_scope"] == SCOPE_NAME


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
