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

from flip.nvflare.server_port import main, set_server_port

KIT = {
    "format_version": 2,
    "servers": [{"name": "net-1", "service": {"target": "fl.app.flip.aicentre.co.uk:8002", "scheme": "http"}}],
    "client": {"retry_timeout": 30},
}


@pytest.fixture
def fed_client(tmp_path):
    path = tmp_path / "startup" / "fed_client.json"
    path.parent.mkdir()
    path.write_text(json.dumps(KIT))
    return path


def _target(path):
    return json.loads(path.read_text())["servers"][0]["service"]["target"]


def test_the_port_changes_and_the_host_and_the_rest_stay(fed_client):
    assert set_server_port(fed_client, "443") == "8002"

    config = json.loads(fed_client.read_text())
    assert config["servers"][0]["service"] == {"target": "fl.app.flip.aicentre.co.uk:443", "scheme": "http"}
    assert config["client"] == KIT["client"]
    assert not (fed_client.parent / "fed_client.json.tmp").exists()


def test_the_same_port_leaves_the_file_untouched(fed_client):
    before = fed_client.read_text()
    assert set_server_port(fed_client, "8002") is None
    assert fed_client.read_text() == before


@pytest.mark.parametrize("port", ["", "0", "65536", "443a", "-1"])
def test_a_bad_port_is_refused(fed_client, port):
    with pytest.raises(ValueError, match="FL_EDGE_PORT must be a port number"):
        set_server_port(fed_client, port)


def test_a_signed_kit_is_refused_rather_than_broken(fed_client):
    (fed_client.parent / "signature.json").write_text("{}")
    with pytest.raises(ValueError, match="signed kit"):
        set_server_port(fed_client, "443")
    assert _target(fed_client).endswith(":8002")


def test_a_signed_kit_already_on_the_port_still_starts(fed_client):
    (fed_client.parent / "signature.json").write_text("{}")
    assert set_server_port(fed_client, "8002") is None


def test_a_kit_without_a_server_target_is_refused(fed_client):
    fed_client.write_text(json.dumps({"servers": []}))
    with pytest.raises(ValueError, match="no servers"):
        set_server_port(fed_client, "443")


def test_main_without_the_variable_does_nothing(fed_client, monkeypatch):
    monkeypatch.delenv("FL_EDGE_PORT", raising=False)
    assert main([str(fed_client)]) == 0
    assert _target(fed_client).endswith(":8002")


def test_main_applies_the_variable_and_says_so(fed_client, monkeypatch, capsys):
    monkeypatch.setenv("FL_EDGE_PORT", "443")
    assert main([str(fed_client)]) == 0
    assert _target(fed_client).endswith(":443")
    assert "server port 8002 -> 443" in capsys.readouterr().out


def test_main_fails_closed_on_a_refused_port(fed_client, monkeypatch, capsys):
    monkeypatch.setenv("FL_EDGE_PORT", "eighty")
    assert main([str(fed_client)]) == 1
    assert "must be a port number" in capsys.readouterr().err


def test_main_fails_on_a_missing_kit(tmp_path, monkeypatch):
    monkeypatch.setenv("FL_EDGE_PORT", "443")
    assert main([str(tmp_path / "missing.json")]) == 1
