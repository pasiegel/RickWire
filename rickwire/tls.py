"""Self-signed local CA + server cert so the phone PWA gets a secure context (install, share target, service worker).

Install rickwire-ca.crt on the phone once (Settings > Security > Install certificate > CA) and the
LAN address is trusted. The CA is name-constrained to private IP ranges, so it cannot vouch for real sites.
"""
from __future__ import annotations

import datetime as dt
import ipaddress
import json
import socket

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from .config import TLS_DIR
from .security import local_ips

CA_CERT, CA_KEY = TLS_DIR / "rickwire-ca.crt", TLS_DIR / "rickwire-ca.key"
SRV_CERT, SRV_KEY = TLS_DIR / "server.crt", TLS_DIR / "server.key"
SRV_META = TLS_DIR / "server.json"

PERMITTED = [ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8")]


def _write_key(path, key):
    path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                       serialization.NoEncryption()))


def _ca():
    if CA_CERT.exists() and CA_KEY.exists():
        return (x509.load_pem_x509_certificate(CA_CERT.read_bytes()),
                serialization.load_pem_private_key(CA_KEY.read_bytes(), None))
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"RickWire Local CA ({socket.gethostname()})")])
    now = dt.datetime.now(dt.timezone.utc)
    hostname = socket.gethostname().lower()
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(days=1)).not_valid_after(now + dt.timedelta(days=3650))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True)
            .add_extension(x509.NameConstraints(
                permitted_subtrees=[x509.IPAddress(n) for n in PERMITTED] +
                                   [x509.DNSName(hostname), x509.DNSName("localhost"), x509.DNSName(f"{hostname}.local")],
                excluded_subtrees=None), critical=True)
            .sign(key, hashes.SHA256()))
    TLS_DIR.mkdir(parents=True, exist_ok=True)
    _write_key(CA_KEY, key)
    CA_CERT.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return cert, key


def ensure_server_cert() -> tuple[str, str]:
    ips = [i for i in local_ips() if any(ipaddress.ip_address(i) in n for n in PERMITTED)]
    hostname = socket.gethostname().lower()
    want = {"ips": ips, "host": hostname}
    if SRV_CERT.exists() and SRV_KEY.exists() and SRV_META.exists():
        try:
            if json.loads(SRV_META.read_text()) == want:
                cert = x509.load_pem_x509_certificate(SRV_CERT.read_bytes())
                if cert.not_valid_after_utc > dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=30):
                    return str(SRV_CERT), str(SRV_KEY)
        except (ValueError, OSError):
            pass
    ca_cert, ca_key = _ca()
    key = ec.generate_private_key(ec.SECP256R1())
    now = dt.datetime.now(dt.timezone.utc)
    sans = [x509.DNSName(hostname), x509.DNSName(f"{hostname}.local"), x509.DNSName("localhost"),
            x509.IPAddress(ipaddress.ip_address("127.0.0.1"))] + [x509.IPAddress(ipaddress.ip_address(i)) for i in ips]
    cert = (x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"RickWire {hostname}")]))
            .issuer_name(ca_cert.subject).public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(days=1)).not_valid_after(now + dt.timedelta(days=397))
            .add_extension(x509.SubjectAlternativeName(sans), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .sign(ca_key, hashes.SHA256()))
    _write_key(SRV_KEY, key)
    SRV_CERT.write_bytes(cert.public_bytes(serialization.Encoding.PEM) + ca_cert.public_bytes(serialization.Encoding.PEM))
    SRV_META.write_text(json.dumps(want))
    return str(SRV_CERT), str(SRV_KEY)
