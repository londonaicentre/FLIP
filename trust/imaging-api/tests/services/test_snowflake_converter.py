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
"""``SnowflakeConverter``: DICOM to NIfTI through SPCS job services (FLIP#1583).

The Snowflake connector is mocked throughout. These are unit tests of the contract this
service has with the two owner's-rights procedures and with XNAT: what it CALLs, how it
reads a status that is allowed to be empty, which of the converter's files it will upload,
and which REST calls register them. None of it needs an account, a token or a network.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from imaging_api.config import Settings, get_settings
from imaging_api.services.snowflake_converter import (
    DONE,
    FAILED,
    PENDING,
    RUNNING,
    ConversionError,
    ScanRef,
    SnowflakeConverter,
    enabled,
    interpret_status,
)

SCAN = ScanRef(project="XA", session="ACC-E1", scan="3", experiment="E1")


class FakeCursor:
    """A cursor that answers each CALL from a scripted queue and records what it ran."""

    def __init__(self, answers: list[Any], log: list[tuple[str, tuple]]) -> None:
        self._answers = answers
        self._log = log
        self._row: tuple | None = None

    def execute(self, sql: str, params: tuple = ()) -> None:
        self._log.append((sql, params))
        answer = self._answers.pop(0) if self._answers else None
        if isinstance(answer, Exception):
            raise answer
        self._row = None if answer is None else (answer,)

    def fetchone(self) -> tuple | None:
        return self._row

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *_: object) -> None:
        return None


class FakeConnection:
    def __init__(self, answers: list[Any]) -> None:
        self.answers = answers
        self.calls: list[tuple[str, tuple]] = []
        self.closed = False

    def cursor(self) -> FakeCursor:
        return FakeCursor(self.answers, self.calls)

    def close(self) -> None:
        self.closed = True


def build(tmp_path: Path, answers: list[Any], **overrides: Any) -> tuple[SnowflakeConverter, FakeConnection]:
    """A converter wired to a fake Snowflake and a real temporary output tree."""
    settings = get_settings().model_copy(
        update={
            "CONVERTER_BACKEND": "snowflake",
            "SNOWFLAKE_DATABASE": "FLIP_TRUST_GSTT",
            "SNOWFLAKE_SCHEMA": "APP",
            "CONVERTER_IO_DIR": str(tmp_path),
            "CONVERT_POLL_SECONDS": 1,
            "CONVERT_TIMEOUT_SECONDS": 10,
            **overrides,
        }
    )
    connection = FakeConnection(answers)
    elapsed = [0.0]
    converter = SnowflakeConverter(
        settings=settings,
        connect=lambda _: connection,
        sleep=lambda seconds: elapsed.__setitem__(0, elapsed[0] + seconds),
        clock=lambda: elapsed[0],
    )
    return converter, connection


#
# Status: an empty answer is "not scheduled yet", never a failure
#


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("[]", PENDING),
        ("", PENDING),
        ("   ", PENDING),
        ('[{"status": "PENDING"}]', RUNNING),
        ('[{"status": "READY"}]', RUNNING),
        ('[{"status": "DONE"}]', DONE),
        ('[{"status": "FAILED", "message": "boom"}]', FAILED),
        ('[{"status": "DONE"}, {"status": "FAILED"}]', FAILED),
        ('[{"status": "DONE"}, {"status": "READY"}]', RUNNING),
        ('{"status": "DONE"}', DONE),
    ],
)
def test_status_is_read_the_way_spcs_writes_it(raw: str, expected: str) -> None:
    assert interpret_status(raw) == expected


def test_an_empty_status_is_never_failed() -> None:
    """The single most costly misreading on this path.

    SYSTEM$GET_SERVICE_STATUS answers '[]' for a job that exists and has not been
    scheduled yet (FLIP#1578). Calling that FAILED relaunches a healthy conversion on
    every poll, which on a warm pool is an unbounded loop of job services.
    """
    assert interpret_status("[]") != FAILED


def test_unparseable_status_degrades_to_pending() -> None:
    assert interpret_status("not json at all") == PENDING


def test_a_rejected_identifier_is_raised_not_reported_as_a_state() -> None:
    with pytest.raises(ConversionError, match="rejected"):
        interpret_status("rejected")


#
# Launching
#


def test_launch_calls_the_procedure_with_bound_parameters(tmp_path: Path) -> None:
    """Bound, never interpolated: a value spliced into the CALL text never reaches the
    procedure's own identifier validation, which is the only thing standing between a
    caller and the dynamic SQL the procedure builds."""
    converter, connection = build(tmp_path, ["launched"])

    assert converter.launch(SCAN) == "launched"

    sql, params = connection.calls[0]
    assert sql == "CALL FLIP_TRUST_GSTT.APP.CONVERT_SCAN(%s, %s, %s)"
    assert params == ("XA", "ACC-E1", "3")
    assert "XA" not in sql, "the identifiers must not be interpolated into the statement"


@pytest.mark.parametrize(
    "bad",
    [
        ScanRef(project="XA; DROP", session="S", scan="1", experiment="E1"),
        ScanRef(project="XA", session="../../etc", scan="1", experiment="E1"),
        ScanRef(project="XA", session="S", scan="1 2", experiment="E1"),
        ScanRef(project="", session="S", scan="1", experiment="E1"),
        ScanRef(project="X" * 65, session="S", scan="1", experiment="E1"),
    ],
)
def test_a_bad_identifier_is_refused_before_anything_is_called(tmp_path: Path, bad: ScanRef) -> None:
    """The procedure refuses these too; refusing here names which one, and costs no round trip."""
    converter, connection = build(tmp_path, ["launched"])

    with pytest.raises(ConversionError):
        converter.launch(bad)
    assert connection.calls == [], "a refused scan reached Snowflake"


def test_a_procedure_that_rejects_is_an_error_not_a_silent_success(tmp_path: Path) -> None:
    converter, _ = build(tmp_path, ["rejected"])

    with pytest.raises(ConversionError, match="rejected"):
        converter.launch(SCAN)


def test_a_procedure_returning_no_row_is_an_error(tmp_path: Path) -> None:
    converter, _ = build(tmp_path, [None])

    with pytest.raises(ConversionError, match="returned no row"):
        converter.launch(SCAN)


#
# Waiting
#


def test_wait_polls_until_the_job_is_terminal(tmp_path: Path) -> None:
    converter, connection = build(tmp_path, ["[]", '[{"status": "READY"}]', '[{"status": "DONE"}]'])

    assert converter.wait(SCAN) == DONE
    assert len(connection.calls) == 3
    assert all(call[0].endswith("CONVERT_STATUS(%s, %s, %s)") for call in connection.calls)


def test_wait_returns_the_failure_as_soon_as_it_is_known(tmp_path: Path) -> None:
    converter, connection = build(tmp_path, ['[{"status": "FAILED"}]', '[{"status": "DONE"}]'])

    assert converter.wait(SCAN) == FAILED
    assert len(connection.calls) == 1, "it kept polling after a terminal state"


def test_wait_gives_up_on_the_budget_without_claiming_failure(tmp_path: Path) -> None:
    """A conversion that outlives the budget is still running; reporting FAILED would make
    the caller relaunch a job that is about to write its output."""
    converter, _ = build(tmp_path, ["[]"] * 50, CONVERT_TIMEOUT_SECONDS=3, CONVERT_POLL_SECONDS=1)

    assert converter.wait(SCAN) == PENDING


#
# The converter's output
#


def _write(directory: Path, names: list[str]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for name in names:
        (directory / name).write_bytes(b"x")


def test_outputs_are_the_converter_files_under_this_scan_only(tmp_path: Path) -> None:
    converter, _ = build(tmp_path, [])
    _write(tmp_path / "XA" / "ACC-E1" / "3", ["a.nii.gz", "a.json", "a.bval", "a.bvec"])
    _write(tmp_path / "XA" / "ACC-E1" / "4", ["other.nii.gz"])

    names = [path.name for path in converter.outputs(SCAN)]

    assert names == ["a.bval", "a.bvec", "a.json", "a.nii.gz"]


def test_outputs_ignore_everything_that_is_not_converter_output(tmp_path: Path) -> None:
    """The I/O tree is shared storage. Uploading whatever is in it would put unreviewed
    bytes into the archive under the trust's own NIFTI resource."""
    directory = tmp_path / "XA" / "ACC-E1" / "3"
    _write(directory, ["a.nii.gz", "notes.txt", ".partial.nii.gz", "dcm2niix.log"])
    (directory / "subdir").mkdir()
    converter, _ = build(tmp_path, [])

    assert [path.name for path in converter.outputs(SCAN)] == ["a.nii.gz"]


def test_outputs_of_a_scan_that_was_never_converted_are_empty(tmp_path: Path) -> None:
    converter, _ = build(tmp_path, [])

    assert converter.outputs(SCAN) == []


def test_the_output_path_matches_what_the_procedure_writes(tmp_path: Path) -> None:
    """Terraform's CONVERT_SCAN mounts @CONVERTER_IO/<project>/<session>/<scan> as the
    job's /output. A disagreement here is a conversion that succeeds and registers nothing."""
    converter, _ = build(tmp_path, [])

    assert converter.output_dir(SCAN) == tmp_path / "XA" / "ACC-E1" / "3"


#
# Registration in XNAT
#


@pytest.fixture
def xnat_put() -> Any:
    with patch("imaging_api.services.snowflake_converter.requests.put") as put:
        put.return_value = MagicMock(status_code=201, text="")
        yield put


def test_registering_creates_the_resource_then_uploads_each_file(tmp_path: Path, xnat_put: Any) -> None:
    """The two calls XNAT's Container Service upload step made, in the same order."""
    converter, _ = build(tmp_path, [])
    _write(tmp_path / "XA" / "ACC-E1" / "3", ["a.nii.gz", "a.json"])

    assert converter.collect(SCAN, {"Cookie": "x", "Content-Type": "application/json"}) == 2

    urls = [call.args[0] for call in xnat_put.call_args_list]
    assert urls[0].endswith("/data/experiments/E1/scans/3/resources/NIFTI?format=NIFTI&content=NIFTI_RAW")
    assert "/resources/NIFTI/files/a.json?" in urls[1]
    assert "inbody=true" in urls[1]
    assert "/resources/NIFTI/files/a.nii.gz?" in urls[2]


def test_the_resource_is_addressed_by_experiment_id_not_session_label(tmp_path: Path, xnat_put: Any) -> None:
    """The two identifiers are different things and both appear on this path: the archive
    path the job mounts is the LABEL, the REST resource is the experiment ID."""
    converter, _ = build(tmp_path, [])
    _write(tmp_path / "XA" / "ACC-E1" / "3", ["a.nii.gz"])

    converter.collect(SCAN, {"Cookie": "x"})

    for call in xnat_put.call_args_list:
        assert "/experiments/E1/" in call.args[0]
        assert "/experiments/ACC-E1/" not in call.args[0]


def test_a_json_content_type_is_not_sent_with_the_bytes(tmp_path: Path, xnat_put: Any) -> None:
    """Every other XNAT call this service makes carries application/json. Sending it with
    a NIfTI body makes XNAT store a file it then cannot serve."""
    converter, _ = build(tmp_path, [])
    _write(tmp_path / "XA" / "ACC-E1" / "3", ["a.nii.gz"])

    converter.collect(SCAN, {"Cookie": "x", "Content-Type": "application/json"})

    resource_call, file_call = xnat_put.call_args_list
    assert resource_call.kwargs["headers"]["Content-Type"] == "application/json"
    assert "Content-Type" not in file_call.kwargs["headers"]
    assert file_call.kwargs["headers"]["Cookie"] == "x"


def test_an_existing_resource_is_not_an_error(tmp_path: Path, xnat_put: Any) -> None:
    """409 is the re-run case: the scan was converted before and is being replaced."""
    converter, _ = build(tmp_path, [])
    _write(tmp_path / "XA" / "ACC-E1" / "3", ["a.nii.gz"])
    xnat_put.side_effect = [MagicMock(status_code=409, text="exists"), MagicMock(status_code=200, text="")]

    assert converter.collect(SCAN, {"Cookie": "x"}) == 1


def test_a_refused_resource_fails_loudly(tmp_path: Path, xnat_put: Any) -> None:
    converter, _ = build(tmp_path, [])
    _write(tmp_path / "XA" / "ACC-E1" / "3", ["a.nii.gz"])
    xnat_put.return_value = MagicMock(status_code=403, text="no")

    with pytest.raises(ConversionError, match="403"):
        converter.collect(SCAN, {"Cookie": "x"})


def test_a_refused_file_fails_loudly(tmp_path: Path, xnat_put: Any) -> None:
    converter, _ = build(tmp_path, [])
    _write(tmp_path / "XA" / "ACC-E1" / "3", ["a.nii.gz"])
    xnat_put.side_effect = [MagicMock(status_code=201, text=""), MagicMock(status_code=500, text="boom")]

    with pytest.raises(ConversionError, match="a.nii.gz"):
        converter.collect(SCAN, {"Cookie": "x"})


def test_a_conversion_that_produced_nothing_is_not_registered_as_a_success(tmp_path: Path, xnat_put: Any) -> None:
    """A dcm2niix that reports DONE having written nothing (an unreadable series) must not
    leave an empty NIFTI resource behind: reconvert_nifti would then stop selecting the
    scan, and the trust would hold a session whose NIfTI does not exist."""
    converter, _ = build(tmp_path, [])

    with pytest.raises(ConversionError, match="no NIfTI"):
        converter.collect(SCAN, {"Cookie": "x"})
    xnat_put.assert_not_called()


#
# Connecting
#


def test_the_session_token_is_the_credential_and_there_is_no_password(tmp_path: Path) -> None:
    """authenticator='oauth'. Anything else sends the token as a password and is refused."""
    token = tmp_path / "token"
    token.write_text("the-session-token\n")
    settings = get_settings().model_copy(
        update={
            "CONVERTER_BACKEND": "snowflake",
            "SNOWFLAKE_TOKEN_FILE": str(token),
            "SNOWFLAKE_HOST": "host.example",
            "SNOWFLAKE_ACCOUNT": "ACC",
            "SNOWFLAKE_DATABASE": "FLIP_TRUST_GSTT",
        }
    )
    connector = MagicMock()
    with patch.dict("sys.modules", {"snowflake": MagicMock(connector=connector), "snowflake.connector": connector}):
        from imaging_api.services.snowflake_converter import _connect

        _connect(settings)

    kwargs = connector.connect.call_args.kwargs
    assert kwargs["authenticator"] == "oauth"
    assert kwargs["token"] == "the-session-token"
    assert "password" not in kwargs
    assert "private_key" not in kwargs


def test_a_missing_session_token_names_the_spec_field_that_is_missing(tmp_path: Path) -> None:
    """An imaging-api deployed without mountSessionToken looks exactly like this, and the
    error it would otherwise raise names a file path nobody put there by hand."""
    settings = get_settings().model_copy(
        update={"CONVERTER_BACKEND": "snowflake", "SNOWFLAKE_TOKEN_FILE": str(tmp_path / "absent")}
    )
    from imaging_api.services.snowflake_converter import _connect

    with pytest.raises(ConversionError, match="mountSessionToken"):
        _connect(settings)


def test_the_connection_is_opened_once_and_reused(tmp_path: Path) -> None:
    opened: list[Settings] = []
    connection = FakeConnection(["launched", '[{"status": "DONE"}]'])

    def connect(settings: Settings) -> FakeConnection:
        opened.append(settings)
        return connection

    converter = SnowflakeConverter(
        settings=get_settings().model_copy(
            update={"SNOWFLAKE_DATABASE": "D", "SNOWFLAKE_SCHEMA": "APP", "CONVERTER_IO_DIR": str(tmp_path)}
        ),
        connect=connect,
    )

    converter.launch(SCAN)
    converter.status(SCAN)

    assert len(opened) == 1
    converter.close()
    assert connection.closed is True


#
# The gate
#


def test_the_feature_is_off_unless_a_trust_asks_for_it() -> None:
    """Compose and Kubernetes must keep the Container Service path byte for byte."""
    assert get_settings().CONVERTER_BACKEND == "container-service"
    assert enabled(get_settings()) is False
    assert enabled(get_settings().model_copy(update={"CONVERTER_BACKEND": "snowflake"})) is True


def test_the_status_contract_is_documented_in_json_terms() -> None:
    """A regression guard on the shape, not the module: if SPCS ever answered a bare
    object rather than a list, both readings must still work."""
    assert interpret_status(json.dumps([{"status": "DONE"}])) == DONE
    assert interpret_status(json.dumps({"status": "DONE"})) == DONE
