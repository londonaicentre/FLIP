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

"""One NVFLARE admin session per study (FLIP#1678).

Each FLIP project is an NVFLARE study, so a job a trust runs in Kubernetes job pods gets only its own project's data.
NVFLARE binds a session to one study at login: a job is submitted in its session's study, and listing, aborting or
downloading a job works only from a session in that study. The default session cannot see study jobs at all.
"""

import threading
from collections import OrderedDict
from collections.abc import Callable, Sequence
from pathlib import Path

from cryptography import x509
from cryptography.x509.oid import NameOID
from nvflare.apis.job_def import DEFAULT_STUDY
from nvflare.fuel.flare_api.api_spec import AuthenticationError

from fl_api.utils.flip_session import FLIP_Session
from fl_api.utils.logger import logger

# What the server answers a login to a study it does not have: never registered, or lost with its registry.
_UNKNOWN_STUDY_CODES = {"AUTH_UNKNOWN_STUDY", "AUTH_STUDY_NOT_CONFIGURED"}


class UnknownStudy(Exception):
    """The FL server has no such study, so it holds no jobs in it."""


def admin_org(startup_dir: Path) -> str:
    """The organisation of this admin's certificate.

    A project_admin enrols sites in a study as ``<org>:<site>``. FLIP provisions every site of a net in the admin's org
    (``add-client.sh`` clones the first client), so the admin's own is every site's.

    Args:
        startup_dir (Path): The admin kit's ``startup`` directory.

    Returns:
        str: The certificate's ``O``.

    Raises:
        ValueError: if the certificate names no organisation.
    """
    cert = x509.load_pem_x509_certificate((startup_dir / "client.crt").read_bytes())
    orgs = cert.subject.get_attributes_for_oid(NameOID.ORGANIZATION_NAME)
    if not orgs:
        raise ValueError(f"the admin certificate in {startup_dir} names no organisation")
    return str(orgs[0].value)


class StudySessions:
    """The default admin session, plus one per study, opened on first use and evicted least recently used.

    Args:
        default (FLIP_Session): The session in the default study, opened at startup.
        factory (Callable[[str], FLIP_Session]): Opens a connected session in a study.
        startup_dir (Path): The admin kit's ``startup`` directory, for the admin's organisation.
        max_sessions (int): How many study sessions stay open.
    """

    def __init__(
        self,
        default: FLIP_Session,
        factory: Callable[[str], FLIP_Session],
        startup_dir: Path,
        max_sessions: int = 32,
    ) -> None:
        self.default = default
        self._factory = factory
        self._startup_dir = startup_dir
        self._max_sessions = max_sessions
        self._sessions: OrderedDict[str, FLIP_Session] = OrderedDict()
        self._org: str | None = None
        self._lock = threading.Lock()

    def get(self, study: str | None) -> FLIP_Session:
        """The session for a study; the default one for no study.

        Raises:
            UnknownStudy: if the server has no such study.
        """
        if not study or study == DEFAULT_STUDY:
            return self.default
        with self._lock:
            if study in self._sessions:
                self._sessions.move_to_end(study)
                return self._sessions[study]
            try:
                session = self._factory(study)
            except AuthenticationError as e:
                if e.auth_code in _UNKNOWN_STUDY_CODES:
                    raise UnknownStudy(study) from e
                raise
            self._sessions[study] = session
            while len(self._sessions) > self._max_sessions:
                _, evicted = self._sessions.popitem(last=False)
                evicted.close()
            return session

    def register(self, study: str, sites: Sequence[str]) -> None:
        """Create the study with these sites, or add them to it. Safe to repeat: NVFLARE merges."""
        if self._org is None:
            self._org = admin_org(self._startup_dir)
        self.default.register_study(study, site_orgs=[f"{self._org}:{site}" for site in sites])
        logger.info(f"Study {study} registered with sites {list(sites)}")

    def close_all(self) -> None:
        """Close every study session (not the default one)."""
        with self._lock:
            while self._sessions:
                _, session = self._sessions.popitem()
                session.close()
