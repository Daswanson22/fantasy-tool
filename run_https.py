"""
Run the Django dev server over HTTPS using uvicorn + a self-signed cert.
Works on Python 3.12+ (Windows/macOS/Linux).

Usage:
    python run_https.py
"""
import datetime
import ipaddress
import subprocess
import sys
from pathlib import Path

CERT_FILE = Path("localhost.crt")
KEY_FILE = Path("localhost.key")


def generate_cert():
    """Generate a self-signed cert for localhost using the cryptography package."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    print("Generating self-signed certificate for localhost...")
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "localhost"),
    ])

    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.now(datetime.timezone.utc))
        .not_valid_after(datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=365))
        .add_extension(
            x509.SubjectAlternativeName([
                x509.DNSName("localhost"),
                x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
            ]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )

    KEY_FILE.write_bytes(key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ))
    CERT_FILE.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    print(f"Certificate saved: {CERT_FILE}, {KEY_FILE}")


if not CERT_FILE.exists() or not KEY_FILE.exists():
    generate_cert()

print("Starting HTTPS server at https://127.0.0.1:8000/")
print("Accept the browser's self-signed cert warning (Advanced > Proceed).")
print()

subprocess.run([
    sys.executable, "-m", "uvicorn",
    "fantasy_tool.asgi:application",
    f"--ssl-keyfile={KEY_FILE}",
    f"--ssl-certfile={CERT_FILE}",
    "--host=127.0.0.1",
    "--port=8000",
    "--reload",
])
