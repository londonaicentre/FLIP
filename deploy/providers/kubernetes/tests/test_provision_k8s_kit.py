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

"""Unit tests for provision_k8s_kit.py (FLIP#593 pt.2).

A throwaway project CA is generated in-test, so the suite has no dependency on
the gitignored dev workspace. Covers the two failure modes #593 calls out:
the SAN-bearing client cert, and the flat RSA-PSS/SHA256 signature.json with no
format_version wrapper."""

import importlib.util
import json
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID

_SCRIPT = Path(__file__).resolve().parents[1] / "provision_k8s_kit.py"
_spec = importlib.util.spec_from_file_location("provision_k8s_kit", _SCRIPT)
pk = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pk)


def _make_ca(tmp_path: Path) -> Path:
    """Write a throwaway state/cert.json (root_cert + root_pri_key) and return its path."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "net-1")])
    import datetime
    cert = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name)
        .public_key(key.public_key()).serial_number(1)
        .not_valid_before(datetime.datetime(2026, 1, 1))
        .not_valid_after(datetime.datetime(2030, 1, 1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    cert_json = tmp_path / "cert.json"
    cert_json.write_text(json.dumps({
        "root_cert": cert.public_bytes(serialization.Encoding.PEM).decode(),
        "root_pri_key": key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption()).decode(),
    }))
    return cert_json


def _template_startup(tmp_path: Path) -> Path:
    d = tmp_path / "tmpl" / "startup"
    d.mkdir(parents=True)
    for s in ("start.sh", "sub_start.sh", "stop_fl.sh"):
        (d / s).write_text(f"#!/bin/sh\n# {s}\n")
    return d


def test_client_cert_has_san_and_is_ca_signed(tmp_path):
    root_pem, root_key = pk.load_ca(_make_ca(tmp_path))
    cert_pem, key_pem = pk.generate_client_cert(root_pem, root_key, "Trust_K8s")
    cert = x509.load_pem_x509_certificate(cert_pem.encode())
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert san.get_values_for_type(x509.DNSName) == ["Trust_K8s"]       # the make-or-break SAN
    assert cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value == "Trust_K8s"
    # signed by the CA:
    root = x509.load_pem_x509_certificate(root_pem.encode())
    root.public_key().verify(cert.signature, cert.tbs_certificate_bytes,
                             padding.PKCS1v15(), cert.signature_hash_algorithm)


def test_signature_json_is_flat_and_verifies(tmp_path):
    cert_json = _make_ca(tmp_path)
    files = pk.build_kit(cert_json, "Trust_K8s", "net-1", "fl-server-net-1", _template_startup(tmp_path))
    sigs = json.loads(files["signature.json"])
    assert "format_version" not in sigs                                 # wrapper crashes NVFLARE
    assert all(isinstance(v, str) for v in sigs.values())               # flat {name: b64}
    # signature.json signs every *other* startup file, not itself:
    assert set(sigs) == {n for n in files if n != "signature.json"}
    root_pem, _ = pk.load_ca(cert_json)
    pub = x509.load_pem_x509_certificate(root_pem.encode()).public_key()
    import base64
    for name, b64 in sigs.items():
        pub.verify(base64.b64decode(b64), files[name],
                   padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.MAX_LENGTH),
                   hashes.SHA256())


def test_fed_client_json_targets_server_identity(tmp_path):
    files = pk.build_kit(_make_ca(tmp_path), "Trust_K8s", "net-1", "fl-server-net-1", _template_startup(tmp_path))
    fc = json.loads(files["fed_client.json"])
    assert fc["client"]["fqsn"] == "Trust_K8s"
    assert fc["client"]["connection_security"] == "mtls"
    assert fc["servers"][0]["identity"] == "fl-server-net-1"
    assert fc["format_version"] == 2


def test_build_kit_includes_all_startup_files(tmp_path):
    files = pk.build_kit(_make_ca(tmp_path), "Trust_K8s", "net-1", "fl-server-net-1", _template_startup(tmp_path))
    assert {"fed_client.json", "client.crt", "client.key", "rootCA.pem",
            "start.sh", "sub_start.sh", "stop_fl.sh", "signature.json"} == set(files)


def test_missing_template_script_raises(tmp_path):
    empty = tmp_path / "empty" / "startup"
    empty.mkdir(parents=True)
    with pytest.raises(FileNotFoundError):
        pk.build_kit(_make_ca(tmp_path), "Trust_K8s", "net-1", "fl-server-net-1", empty)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
