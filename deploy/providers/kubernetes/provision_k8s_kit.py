#!/usr/bin/env python3
#
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

"""Provision an NVFLARE participant kit for a Kubernetes trust slot (FLIP#593 pt.2).

Before this, the Trust_K8s kit was assembled by hand from the project CA in
``state/cert.json`` — an undocumented, error-prone process where two mistakes
silently break the FL connection:

  * the client cert MUST carry ``X509v3 Subject Alternative Name: DNS:<slot>``
    (NVFLARE matches client identity on the SAN; a cert without it registers
    but never connects);
  * ``startup/signature.json`` MUST be a FLAT ``{filename: base64-signature}``
    dict, each signature RSA-PSS(MGF1-SHA256, salt=MAX_LENGTH)/SHA256 over the
    file bytes, signed with the project CA key. A ``format_version`` wrapper
    crashes NVFLARE's SecurityContentService; a stale signature fails the
    secure_train check with the misleading "not secure content".

This script generates that kit deterministically from the project CA so a new
K8s trust slot can be provisioned from documented commands only.

The signing scheme (PSS/MGF1-SHA256/MAX_LENGTH/SHA256) was confirmed by
verifying real NVFLARE-provisioned signatures against the project CA.

Usage:
  python3 provision_k8s_kit.py \
      --cert-json   workspace/net-1/state/cert.json \
      --slot        Trust_K8s \
      --net         net-1 \
      --server-identity fl-server-net-1 \
      --template-startup workspace/net-1/services/Trust_1/startup \
      --out         ./out/Trust_K8s \
      [--s3-uri s3://flipstag-aicentre/fl-flare-participant-kits/20260312/net-1/services/Trust_K8s]
"""

import argparse
import base64
import json
import subprocess
import sys
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID

# NVFLARE startup-kit signing parameters — confirmed against real provisioned
# kits. Do NOT change without re-verifying against NVFLARE's SecurityContentService.
_PSS = padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.MAX_LENGTH)
_HASH = hashes.SHA256()

# The static NVFLARE launcher scripts copied verbatim from a template kit. The
# dynamic, CA-derived files (cert/key/rootCA/fed_client.json) are generated.
_TEMPLATE_SCRIPTS = ("start.sh", "sub_start.sh", "stop_fl.sh")


def load_ca(cert_json_path: Path) -> tuple[str, rsa.RSAPrivateKey]:
    """Return (root_cert_pem, root_private_key) from an NVFLARE state/cert.json."""
    data = json.loads(cert_json_path.read_text())
    root_cert_pem = data["root_cert"]
    root_key = serialization.load_pem_private_key(data["root_pri_key"].encode(), password=None)
    return root_cert_pem, root_key


def generate_client_cert(root_cert_pem: str, root_key: rsa.RSAPrivateKey, slot: str,
                         org: str = "AICentre") -> tuple[str, str]:
    """Generate a client key + cert (CN=slot, O=org, SAN DNS:slot) signed by the CA.

    The SAN is mandatory: NVFLARE matches the client's identity on it.
    Returns (cert_pem, key_pem).
    """
    root_cert = x509.load_pem_x509_certificate(root_cert_pem.encode())
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, slot),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, org),
    ])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(root_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(root_cert.not_valid_before_utc)
        .not_valid_after(root_cert.not_valid_after_utc)
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(slot)]), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(root_key, _HASH)
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ).decode()
    return cert_pem, key_pem


def render_fed_client_json(slot: str, net: str, server_identity: str) -> str:
    """Render startup/fed_client.json for the slot (mirrors a provisioned kit)."""
    return json.dumps({
        "format_version": 2,
        "servers": [{"name": net, "service": {"scheme": "grpc"}, "identity": server_identity}],
        "client": {
            "ssl_private_key": "client.key",
            "ssl_cert": "client.crt",
            "ssl_root_cert": "rootCA.pem",
            "fqsn": slot,
            "is_leaf": True,
            "connection_security": "mtls",
        },
        "overseer_agent": {
            "path": "nvflare.ha.dummy_overseer_agent.DummyOverseerAgent",
            "args": {"sp_end_point": f"{server_identity}:8002:8003"},
        },
    }, indent=2)


def sign_startup(files: dict[str, bytes], root_key: rsa.RSAPrivateKey) -> dict[str, str]:
    """Return the FLAT signature.json dict: {filename: base64(PSS/SHA256 sig)}.

    No ``format_version`` wrapper — that crashes NVFLARE's SecurityContentService.
    """
    return {
        name: base64.b64encode(root_key.sign(content, _PSS, _HASH)).decode()
        for name, content in files.items()
    }


def build_kit(cert_json: Path, slot: str, net: str, server_identity: str,
              template_startup: Path) -> dict[str, bytes]:
    """Build the full startup/ file set (bytes) for the slot, incl. signature.json."""
    root_cert_pem, root_key = load_ca(cert_json)
    cert_pem, key_pem = generate_client_cert(root_cert_pem, root_key, slot)

    files: dict[str, bytes] = {
        "fed_client.json": render_fed_client_json(slot, net, server_identity).encode(),
        "client.crt": cert_pem.encode(),
        "client.key": key_pem.encode(),
        "rootCA.pem": root_cert_pem.encode(),
    }
    for script in _TEMPLATE_SCRIPTS:
        src = template_startup / script
        if not src.exists():
            raise FileNotFoundError(f"template script missing: {src}")
        files[script] = src.read_bytes()

    # signature.json signs every other startup file; it is not self-signed.
    files["signature.json"] = json.dumps(sign_startup(files, root_key), indent=2).encode()
    return files


def main(cert_json: Path, slot: str, net: str, server_identity: str,
         template_startup: Path, out: Path, s3_uri: str | None) -> None:
    files = build_kit(cert_json, slot, net, server_identity, template_startup)
    startup = out / "startup"
    startup.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        path = startup / name
        path.write_bytes(content)
        # client.key is a private key — keep it 0600 on disk.
        if name == "client.key":
            path.chmod(0o600)
    print(f"✓ Wrote kit for slot '{slot}' → {startup}")
    print(f"  files: {', '.join(sorted(files))}")

    if s3_uri:
        print(f"⇪ Uploading to {s3_uri}/startup/ …")
        subprocess.run(["aws", "s3", "cp", str(startup), f"{s3_uri}/startup/", "--recursive"], check=True)
        print("✓ Uploaded.")
    else:
        print("ⓘ  No --s3-uri given; not uploading. Sync manually with:")
        print(f"     aws s3 cp {startup} <s3-uri>/startup/ --recursive")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Provision an NVFLARE participant kit for a K8s trust slot (#593 pt.2)")
    p.add_argument("--cert-json", type=Path, required=True, help="Path to the project CA state/cert.json")
    p.add_argument("--slot", required=True, help="FL kit slot / client identity (e.g. Trust_K8s)")
    p.add_argument("--net", default="net-1", help="FL network name (default: net-1)")
    p.add_argument("--server-identity", default=None, help="FL server identity (default: fl-server-<net>)")
    p.add_argument("--template-startup", type=Path, required=True,
                   help="An existing slot's startup/ dir to copy the static NVFLARE scripts from")
    p.add_argument("--out", type=Path, required=True, help="Output dir (startup/ is created under it)")
    p.add_argument("--s3-uri", default=None, help="Optional S3 URI to upload the startup/ dir to")
    args = p.parse_args()
    try:
        main(
            cert_json=args.cert_json,
            slot=args.slot,
            net=args.net,
            server_identity=args.server_identity or f"fl-server-{args.net}",
            template_startup=args.template_startup,
            out=args.out,
            s3_uri=args.s3_uri,
        )
    except (FileNotFoundError, KeyError, ValueError) as e:
        print(f"❌ {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)
