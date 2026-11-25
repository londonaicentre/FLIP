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

"""Point an NVFLARE client kit at the FL server port the trust dials (FLIP#2051).

Run by the fl-client's init step (``python -m flip.nvflare.server_port /app/startup/fed_client.json``).
NVFLARE takes the server address only from ``servers[0].service.target`` in the kit's
``startup/fed_client.json``, and nothing in ``local/`` or the environment overrides it. The trust's kit
file names the port (``FL_EDGE_PORT``, or the site's own ``FL_EDGE_PORT_OVERRIDE``), so this sets the
target's port to it, leaving the host alone. With ``FL_EDGE_PORT`` unset it does nothing.

NVFLARE 2.9 does not sign a plain kit, so the edit is safe. A kit that carries ``startup/signature.json``
(made by an older provisioner) would then fail its tamper check, so that case is refused instead.
"""

import json
import os
import sys
from pathlib import Path

ENV_VAR = "FL_EDGE_PORT"


def set_server_port(fed_client: Path, port: str) -> str | None:
    """Set the port of the kit's server target.

    Args:
        fed_client (Path): The kit's ``startup/fed_client.json``.
        port (str): The port to dial, as given in the environment.

    Returns:
        str | None: The previous port when the target changed, else None.

    Raises:
        ValueError: If the port is not a TCP port number, the file has no server target, or a signed
            kit would need changing.
    """
    if not port.isdigit() or not 0 < int(port) < 65536:
        raise ValueError(f"{ENV_VAR} must be a port number, got {port!r}")
    config = json.loads(fed_client.read_text())
    try:
        service = config["servers"][0]["service"]
        host, sep, old = str(service["target"]).rpartition(":")
    except (KeyError, IndexError, TypeError, AttributeError) as exc:
        raise ValueError(f"{fed_client} has no servers[0].service.target") from exc
    if not sep:
        raise ValueError(f"{fed_client}: target {service['target']!r} has no port")
    if old == port:
        return None
    if (fed_client.parent / "signature.json").exists():
        raise ValueError(
            f"{fed_client.parent} is a signed kit, so its server port cannot be changed to {port}: "
            "use a kit provisioned by NVFLARE 2.9 or later"
        )
    service["target"] = f"{host}:{port}"
    tmp = fed_client.with_name(fed_client.name + ".tmp")
    tmp.write_text(json.dumps(config, indent=2) + "\n")
    os.replace(tmp, fed_client)
    return old


def main(argv: list[str] | None = None) -> int:
    """Apply ``FL_EDGE_PORT`` to the kit named on the command line.

    Args:
        argv (list[str] | None): The arguments, the path of ``fed_client.json``; ``sys.argv[1:]`` if None.

    Returns:
        int: 0 on success, 1 on a refused port or kit.
    """
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: python -m flip.nvflare.server_port <kit>/startup/fed_client.json", file=sys.stderr)
        return 1
    port = os.environ.get(ENV_VAR, "").strip()
    if not port:
        return 0
    fed_client = Path(args[0])
    try:
        old = set_server_port(fed_client, port)
    except (OSError, ValueError) as exc:
        print(f"[fl-client-init] {exc}", file=sys.stderr)
        return 1
    if old is not None:
        print(f"[fl-client-init] {fed_client}: server port {old} -> {port} ({ENV_VAR})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
