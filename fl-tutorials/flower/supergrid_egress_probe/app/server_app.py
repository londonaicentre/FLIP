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

"""ServerApp that maps SuperGrid's egress allowlist and exits (no FL rounds)."""

from logging import INFO

import requests
from flwr.app import Context
from flwr.common import log
from flwr.serverapp import Grid, ServerApp

# Candidate endpoints: the trackers Flower Labs' guidance implies, the two
# endpoints already observed blocked (as regression controls), and a known-good
# control (PyPI — runtime dependency installation demonstrably works).
CANDIDATES = [
    ("pypi (control, expected OK)", "https://pypi.org/simple/"),
    ("wandb api", "https://api.wandb.ai/graphql"),
    ("wandb site", "https://wandb.ai/"),
    ("dagshub (hosted mlflow)", "https://dagshub.com/"),
    ("databricks community", "https://community.cloud.databricks.com/"),
    ("comet", "https://www.comet.com/"),
    ("neptune", "https://app.neptune.ai/"),
    ("huggingface", "https://huggingface.co/api/models?limit=1"),
    ("github", "https://api.github.com/"),
    ("sagemaker mlflow (observed 403)", "https://mlflow.sagemaker.eu-west-2.app.aws/"),
    ("aws s3 eu-west-2 (observed blocked)", "https://s3.eu-west-2.amazonaws.com/"),
]

app = ServerApp()


@app.main()
def main(grid: Grid, context: Context) -> None:
    """Probe every candidate endpoint and log one verdict line each."""
    urls = list(CANDIDATES)
    extra = str(context.run_config.get("extra-urls", "") or "")
    urls += [("extra", u.strip()) for u in extra.split(",") if u.strip()]

    log(INFO, "EGRESS PROBE: testing %d endpoints", len(urls))
    for name, url in urls:
        try:
            resp = requests.get(url, timeout=8, allow_redirects=False)
            log(INFO, "EGRESS OK   [%s] %s -> HTTP %s", name, url, resp.status_code)
        except requests.exceptions.ProxyError as e:
            log(INFO, "EGRESS DENY [%s] %s -> proxy refused (%s)", name, url, str(e.__cause__ or e)[:120])
        except Exception as e:  # noqa: BLE001 - verdict line per endpoint, never abort the sweep
            log(INFO, "EGRESS ERR  [%s] %s -> %s: %s", name, url, type(e).__name__, str(e)[:120])
    log(INFO, "EGRESS PROBE COMPLETE")
