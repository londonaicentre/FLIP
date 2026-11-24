# Copyright (c) 2026 Flower Labs GmbH
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
# Vendored from the flwrlabs `supernode-authentication` flwr template's
# generate_creds.py. Kept in-tree
# instead of re-scaffolding at provision time, so Flower provisioning is self-contained
# and reproducible. SERVER_SAN_DNS covers both the standalone (superlink) and
# central-hub (fl-server-net-1/2) topologies so one cert is valid for any dev net.
#
import argparse
import datetime
import ipaddress
import os
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.x509.oid import NameOID

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

# Certificate Validity
VALIDITY_DAYS = 365
KEY_SIZE = 4096

# CA Certificate Details
CA_COUNTRY = "DE"
CA_STATE = "HH"
CA_ORGANIZATION = "CA, Inc."

# Server Certificate Details
SERVER_COUNTRY = "DE"
SERVER_STATE = "HH"
SERVER_ORGANIZATION = "Flower"
SERVER_COMMON_NAME = "localhost"

# Subject Alternative Names (SANs)
# FLOWER_EXTRA_SERVER_SANS (comma-separated DNS names) extends the baked-in
# dev set for non-dev topologies — e.g. the hub ECS deployment needs the
# Cloud Map name (fl-server-net-1.flip.local) for the fl-api's Exec-API
# connection and the public FL DNS (fl.<env>.flip.aicentre.co.uk) for the
# trust SuperNodes (FLIP#566).
SERVER_SAN_DNS = ["localhost", "superlink", "fl-server-net-1", "fl-server-net-2"] + [
    san.strip() for san in os.environ.get("FLOWER_EXTRA_SERVER_SANS", "").split(",") if san.strip()
]
SERVER_SAN_IPS = [
    "127.0.0.1",
    "::1",
    # "xy.xy.xy.xy",  # Add your server's public IP here
]

# Output Directories
CERT_DIR = Path("certificates")
KEY_DIR = Path("keys")

# --------------------------------------------------------------------------


def generate_ca() -> tuple[rsa.RSAPrivateKey, x509.Certificate]:
    """Generate a self-signed CA certificate and private key."""
    # Generate Private Key
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=KEY_SIZE)

    # Generate Name
    subject = issuer = x509.Name(
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, CA_COUNTRY),
            x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, CA_STATE),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, CA_ORGANIZATION),
        ]
    )

    # Generate Certificate
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.now(datetime.timezone.utc))
        .not_valid_after(datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=VALIDITY_DAYS))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        # Python 3.13's default TLS context verifies strictly (VERIFY_X509_STRICT): a CA needs its
        # key usage and key identifier, and the certificate it signs an authority key identifier.
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(private_key.public_key()), critical=False)
        .sign(private_key, hashes.SHA256())
    )

    return private_key, cert


def generate_server_cert(
    ca_key: rsa.RSAPrivateKey, ca_cert: x509.Certificate
) -> tuple[rsa.RSAPrivateKey, x509.Certificate]:
    """Generate a server certificate signed by the CA."""
    # Generate Private Key
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=KEY_SIZE)

    # Generate Name
    subject = x509.Name(
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, SERVER_COUNTRY),
            x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, SERVER_STATE),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, SERVER_ORGANIZATION),
            x509.NameAttribute(NameOID.COMMON_NAME, SERVER_COMMON_NAME),
        ]
    )

    # SANs (Subject Alternative Names)
    alt_names = [x509.DNSName(dns) for dns in SERVER_SAN_DNS] + [
        x509.IPAddress(ipaddress.ip_address(ip)) for ip in SERVER_SAN_IPS
    ]

    # Generate Certificate
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.now(datetime.timezone.utc))
        .not_valid_after(datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=VALIDITY_DAYS))
        .add_extension(
            x509.SubjectAlternativeName(alt_names),
            critical=False,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(private_key.public_key()), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )

    return private_key, cert


def write_supernode_key(number: int) -> None:
    """Generate one generic ECDSA key pair, ``supernode_credentials_<number>{,.pub}``."""
    # Generate ECDSA key (P-384 to match -b 384)
    private_key = ec.generate_private_key(ec.SECP384R1())

    # Both halves in OpenSSH format, as the ssh-keygen this replaced wrote them.
    private_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.OpenSSH,
        encryption_algorithm=serialization.NoEncryption(),
    )

    # Serialize Public Key (OpenSSH format)
    public_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.OpenSSH,
        format=serialization.PublicFormat.OpenSSH,
    )

    # Write files
    priv_path = KEY_DIR / f"supernode_credentials_{number}"
    pub_path = KEY_DIR / f"supernode_credentials_{number}.pub"

    # Write private key
    with open(priv_path, "wb") as f:
        f.write(private_bytes)
    # Set permissions for private key (600) - mainly for Unix, but good practice
    try:
        os.chmod(priv_path, 0o600)
    except Exception:
        pass

    # Write public key
    with open(pub_path, "wb") as f:
        f.write(public_bytes)

    print(f"  - SuperNode {number}: {priv_path}, {pub_path}")


def generate_supernode_keys(num_supernodes: int) -> None:
    """Generate generic ECDSA key pairs for supernodes."""
    KEY_DIR.mkdir(exist_ok=True)

    # Clear existing keys
    for item in KEY_DIR.glob("*"):
        if item.is_file():
            item.unlink()

    print(f"Generating keys for {num_supernodes} supernodes...")

    for i in range(1, num_supernodes + 1):
        write_supernode_key(i)


def add_supernode_key(number: int) -> None:
    """Add one SuperNode key pair beside the existing ones (FLIP#1942).

    A SuperNode key is not signed by the CA, so nothing else is regenerated and every
    credential already handed out stays valid.
    """
    KEY_DIR.mkdir(exist_ok=True)
    # The two exact names: a prefix match would also see supernode_credentials_10 for 1.
    if any((KEY_DIR / f"supernode_credentials_{number}{half}").exists() for half in ("", ".pub")):
        raise SystemExit(f"{KEY_DIR}/supernode_credentials_{number} already exists: refusing to overwrite it.")
    write_supernode_key(number)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Flower credentials")
    parser.add_argument(
        "--supernodes",
        type=int,
        default=2,
        help="Number of SuperNode key pairs to generate",
    )
    parser.add_argument(
        "--add-supernode",
        type=int,
        metavar="NUMBER",
        help="Only add the key pair supernode_credentials_<NUMBER>; certificates and other keys are left alone",
    )
    args = parser.parse_args()

    if args.add_supernode is not None:
        if args.add_supernode < 1:
            parser.error("--add-supernode takes a slot number of 1 or more")
        add_supernode_key(args.add_supernode)
        return

    # --- TLS Certificates ---
    CERT_DIR.mkdir(exist_ok=True)
    # Clear existing certificates
    for item in CERT_DIR.glob("*"):
        if item.is_file():
            item.unlink()

    print("Generating CA certificate...")
    ca_key, ca_cert = generate_ca()

    # Save CA
    with open(CERT_DIR / "ca.key", "wb") as f:
        f.write(
            ca_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )
    os.chmod(CERT_DIR / "ca.key", 0o600)  # private key: owner-only
    with open(CERT_DIR / "ca.crt", "wb") as f:
        f.write(ca_cert.public_bytes(serialization.Encoding.PEM))

    print("Generating Server certificate...")
    server_key, server_cert = generate_server_cert(ca_key, ca_cert)

    # Save Server
    with open(CERT_DIR / "server.key", "wb") as f:
        f.write(
            server_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )
    os.chmod(CERT_DIR / "server.key", 0o600)  # private key: owner-only (wrapper relaxes to 640 for container reads)
    with open(CERT_DIR / "server.pem", "wb") as f:
        f.write(server_cert.public_bytes(serialization.Encoding.PEM))

    print(f"Certificates generated in {CERT_DIR}/")

    # --- SuperNode Keys ---
    generate_supernode_keys(args.supernodes)
    print("Done.")


if __name__ == "__main__":
    main()
