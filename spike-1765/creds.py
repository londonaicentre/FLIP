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

"""Spike #1765 credentials: one CA, two SuperLinks, one wildcard fleet-proxy cert, per-project SuperNode keys.

Usage: uv run --no-project --with cryptography creds.py <out-dir>
"""

import datetime
import ipaddress
import sys
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

PEM = serialization.Encoding.PEM

OUT = Path(sys.argv[1])
NOW = datetime.datetime.now(datetime.timezone.utc)


def _pem_key(key) -> bytes:
    return key.private_bytes(PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Spike 1765 Flower CA")])
ca = (
    x509.CertificateBuilder()
    .subject_name(ca_name)
    .issuer_name(ca_name)
    .public_key(ca_key.public_key())
    .serial_number(x509.random_serial_number())
    .not_valid_before(NOW)
    .not_valid_after(NOW + datetime.timedelta(days=30))
    .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
    .sign(ca_key, hashes.SHA256())
)
_write(OUT / "ca.crt", ca.public_bytes(serialization.Encoding.PEM))


def server_cert(name: str, sans: list[str], ips: tuple[str, ...] = ()) -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, sans[0])]))
        .issuer_name(ca_name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(NOW)
        .not_valid_after(NOW + datetime.timedelta(days=30))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName(s) for s in sans] + [x509.IPAddress(ipaddress.ip_address(i)) for i in ips]
            ),
            critical=False,
        )
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    _write(OUT / name / "server.pem", cert.public_bytes(serialization.Encoding.PEM))
    _write(OUT / name / "server.key", _pem_key(key))


# One certificate per SuperLink, named by its in-cluster Service (the fleet-proxy re-encrypts to it), plus
# localhost/127.0.0.1: the SuperLink's own SuperExec dials the Runtime API there over TLS.
for project in ("a", "b"):
    server_cert(
        f"superlink-{project}",
        [f"superlink-{project}", f"superlink-{project}.hub.svc.cluster.local", "localhost"],
        ("127.0.0.1",),
    )
# The trust-facing listener: ONE wildcard certificate covers every project's host name.
server_cert("fleet-proxy", ["*.fleet.flip.test"])

# One SuperNode key pair per (trust, project), OpenSSH format as FLIP's generate_creds.py writes them.
for node in ("trust1-a", "trust1-b"):
    key = ec.generate_private_key(ec.SECP384R1())
    _write(
        OUT / "keys" / node,
        key.private_bytes(PEM, serialization.PrivateFormat.OpenSSH, serialization.NoEncryption()),
    )
    _write(
        OUT / "keys" / f"{node}.pub",
        key.public_key().public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH),
    )
print(f"credentials written to {OUT}")
