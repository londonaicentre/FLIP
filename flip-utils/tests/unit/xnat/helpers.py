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

"""HTTP doubles for the flip.xnat tests.

The client is exercised against a stub session rather than a mocked ``requests`` module, so tests
assert on the URLs actually built — which is where the XNAT REST contract lives.
"""

from typing import Any

from flip.xnat.client import XnatClient


class FakeResponse:
    """Stands in for a ``requests.Response``."""

    def __init__(self, status_code: int = 200, payload: dict[str, Any] | None = None, text: str = "") -> None:
        """
        Args:
            status_code (int): HTTP status to report.
            payload (dict[str, Any] | None): JSON body.
            text (str): Body text, as surfaced in error messages.
        """
        self.status_code = status_code
        self.text = text
        self._payload = payload or {}

    def json(self) -> dict[str, Any]:
        """dict[str, Any]: The JSON body."""
        return self._payload


class FakeSession:
    """Routes GETs by URL substring and records PUTs.

    Routes are matched in insertion order, so register the most specific path first.
    """

    def __init__(self, routes: dict[str, FakeResponse], put_response: FakeResponse | None = None) -> None:
        """
        Args:
            routes (dict[str, FakeResponse]): URL substring to canned response.
            put_response (FakeResponse | None): Response returned from every PUT.
        """
        self.auth: tuple[str, str] | None = None
        self.routes = routes
        self.put_response = put_response or FakeResponse(status_code=200)
        self.puts: list[str] = []
        self.get_error: Exception | None = None

    def get(self, url: str, params: dict[str, str] | None = None, timeout: int | None = None) -> FakeResponse:
        """Return the first route whose key appears in ``url``, else a 404.

        Args:
            url (str): Requested URL.
            params (dict[str, str] | None): Ignored.
            timeout (int | None): Ignored.

        Returns:
            FakeResponse: The matching canned response.

        Raises:
            Exception: ``self.get_error``, when set, to simulate a transport failure.
        """
        if self.get_error is not None:
            raise self.get_error
        for pattern, response in self.routes.items():
            if pattern in url:
                return response
        return FakeResponse(status_code=404, text="not found")

    def put(self, url: str, data: Any = None, timeout: int | None = None) -> FakeResponse:
        """Record the PUT and return the canned response.

        Args:
            url (str): Requested URL.
            data (Any): Ignored (the open file handle).
            timeout (int | None): Ignored.

        Returns:
            FakeResponse: ``self.put_response``.
        """
        self.puts.append(url)
        return self.put_response


def make_client(routes: dict[str, FakeResponse]) -> XnatClient:
    """Build a client wired to a :class:`FakeSession`.

    Args:
        routes (dict[str, FakeResponse]): URL substring to canned response.

    Returns:
        XnatClient: The stubbed client.
    """
    client = XnatClient(server="http://xnat.example", user="u", password="p")
    client._session = FakeSession(routes)  # type: ignore[assignment]
    return client


def project_routes(files: list[str]) -> dict[str, FakeResponse]:
    """Routes for a project with two single-scan experiments (FAK001, FAK002).

    Args:
        files (list[str]): Filenames each scan's NIFTI resource reports.

    Returns:
        dict[str, FakeResponse]: Routes, most specific first.
    """
    return {
        # Most specific first: "/scans" alone would also match the files URL.
        "/resources/NIFTI/files": FakeResponse(payload={"ResultSet": {"Result": [{"Name": n} for n in files]}}),
        "/data/experiments/EXP_1/scans": FakeResponse(payload={"ResultSet": {"Result": [{"ID": "1"}]}}),
        "/data/experiments/EXP_2/scans": FakeResponse(payload={"ResultSet": {"Result": [{"ID": "1"}]}}),
        "/data/projects/PROJ/experiments": FakeResponse(
            payload={
                "ResultSet": {
                    "Result": [
                        {"ID": "EXP_1", "label": "FAK001", "subject_ID": "SUBJ_1"},
                        {"ID": "EXP_2", "label": "FAK002", "subject_ID": "SUBJ_2"},
                    ]
                }
            }
        ),
    }
