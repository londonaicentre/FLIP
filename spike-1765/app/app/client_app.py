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

"""Report the project this SuperNode serves and whether the trust key reached the ClientApp."""

import os
from pathlib import Path

from flwr.app import ConfigRecord, Context, Message, RecordDict
from flwr.clientapp import ClientApp

app = ClientApp()


@app.query()
def query(msg: Message, context: Context) -> Message:
    project = Path("/project/name").read_text().strip()
    # Anything under /projects other than its own would mean another project's data is mounted here.
    others = sorted(p.name for p in Path("/projects").glob("*")) if Path("/projects").exists() else []
    record = ConfigRecord(
        {
            "project": project,
            "key_set": bool(os.environ.get("TRUST_INTERNAL_SERVICE_KEY")),
            "other": ",".join(others) or "none",
        }
    )
    return Message(RecordDict({"r": record}), reply_to=msg)
