"""Пароли, сессии, токены и внутренний CA для mTLS между мастером и нодами."""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from . import config, db

# ------------------------------------------------------------------ пароли
_SCRYPT = dict(n=2**14, r=8, p=1, dklen=32)


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(dk).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt_b64, dk_b64 = stored.split("$", 2)
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(dk_b64)
        dk = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
        return hmac.compare_digest(dk, expected)
    except Exception:
        return False


# ------------------------------------------------------------------ токены
def new_token(nbytes: int = 24) -> str:
    return secrets.token_urlsafe(nbytes)


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def token_hash(token: str) -> str:
    # ключ подмешиваем, чтобы хеши нельзя было пересчитать по радужным таблицам
    return hmac.new(config.SECRET.encode(), token.encode(), hashlib.sha256).hexdigest()


def sign(value: str) -> str:
    return hmac.new(config.SECRET.encode(), value.encode(), hashlib.sha256).hexdigest()


# ------------------------------------------------------------------ сессии
SESSION_TTL = timedelta(days=7)


def create_session(admin_id: int, ua: str = "", ip: str = "") -> str:
    sid = new_token(32)
    exp = (datetime.now(timezone.utc) + SESSION_TTL).strftime("%Y-%m-%dT%H:%M:%SZ")
    db.ex(
        "INSERT INTO sessions(id, admin_id, created_at, expires_at, ua, ip) VALUES(?,?,?,?,?,?)",
        (sid, admin_id, db.now(), exp, ua[:200], ip),
    )
    return sid


def get_session(sid: str | None) -> dict | None:
    if not sid:
        return None
    row = db.q1(
        "SELECT s.*, a.username FROM sessions s JOIN admins a ON a.id=s.admin_id WHERE s.id=?",
        (sid,),
    )
    if not row:
        return None
    if row["expires_at"] < db.now():
        db.ex("DELETE FROM sessions WHERE id=?", (sid,))
        return None
    return dict(row)


def drop_session(sid: str) -> None:
    db.ex("DELETE FROM sessions WHERE id=?", (sid,))


# ------------------------------------------------------------------ CA / mTLS
def _paths() -> tuple:
    return (
        config.CERT_DIR / "ca.key",
        config.CERT_DIR / "ca.pem",
        config.CERT_DIR / "master.key",
        config.CERT_DIR / "master.pem",
    )


def ensure_ca() -> None:
    """Создаёт корневой CA панели (10 лет) и клиентский серт мастера."""
    ca_key, ca_pem, m_key, m_pem = _paths()
    _ensure_ca_only()
    if not m_key.exists() or not m_pem.exists():
        mkey = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        m_key.write_bytes(
            mkey.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        m_pem.write_text(sign_public_key(mkey.public_key(), "hydra-master"), encoding="utf-8")
        try:
            m_key.chmod(0o600)
        except OSError:
            pass


def _ensure_ca_only() -> None:
    ca_key, ca_pem, _, _ = _paths()
    if ca_key.exists() and ca_pem.exists():
        return
    if True:
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, "Hydra Panel CA"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Hydra"),
        ])
        now = datetime.now(timezone.utc)
        cert = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=3650))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(
                x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False
            )
            .add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()),
                critical=False,
            )
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True, content_commitment=False, key_encipherment=False,
                    data_encipherment=False, key_agreement=False, key_cert_sign=True,
                    crl_sign=True, encipher_only=False, decipher_only=False,
                ),
                critical=True,
            )
            .sign(key, hashes.SHA256())
        )
        ca_key.write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        ca_pem.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        for p in (ca_key,):
            try:
                p.chmod(0o600)
            except OSError:
                pass


def _ca() -> tuple:
    _ensure_ca_only()
    ca_key, ca_pem, _, _ = _paths()
    key = serialization.load_pem_private_key(ca_key.read_bytes(), password=None)
    cert = x509.load_pem_x509_certificate(ca_pem.read_bytes())
    return key, cert


def sign_public_key(public_key, cn: str, days: int = 3650) -> str:
    """Подписывает публичный ключ нашим CA (серт мастера для mTLS)."""
    key, cacert = _ca()
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)]))
        .issuer_name(cacert.subject)
        .public_key(public_key)
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=days))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(public_key), critical=False
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(cacert.public_key()),
            critical=False,
        )
        .add_extension(
            x509.ExtendedKeyUsage([
                x509.oid.ExtendedKeyUsageOID.SERVER_AUTH,
                x509.oid.ExtendedKeyUsageOID.CLIENT_AUTH,
            ]),
            critical=False,
        )
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(cn)]), critical=False
        )
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM).decode()


def _san_for(host: str) -> list:
    import ipaddress

    try:
        return [x509.IPAddress(ipaddress.ip_address(host))]
    except ValueError:
        return [x509.DNSName(host)]


def sign_csr(csr_pem: str, cn: str, days: int = 825) -> str:
    key, cacert = _ca()
    csr = x509.load_pem_x509_csr(csr_pem.encode())
    if not csr.is_signature_valid:
        raise ValueError("bad csr signature")
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)]))
        .issuer_name(cacert.subject)
        .public_key(csr.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=days))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(csr.public_key()), critical=False
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(cacert.public_key()),
            critical=False,
        )
        .add_extension(
            x509.ExtendedKeyUsage([
                x509.oid.ExtendedKeyUsageOID.SERVER_AUTH,
                x509.oid.ExtendedKeyUsageOID.CLIENT_AUTH,
            ]),
            critical=False,
        )
        .add_extension(x509.SubjectAlternativeName(_san_for(cn)), critical=False)
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM).decode()


def fingerprint(cert_pem: str) -> str:
    try:
        cert = x509.load_pem_x509_certificate(cert_pem.encode())
        return cert.fingerprint(hashes.SHA256()).hex()
    except Exception:
        return ""


def cert_expires(cert_pem: str) -> str:
    try:
        cert = x509.load_pem_x509_certificate(cert_pem.encode())
        return cert.not_valid_after_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return ""


def gen_csr(cn: str) -> tuple[str, str]:
    """Генерит ключ и CSR для ноды. Возвращает (key_pem, csr_pem)."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)]))
        .sign(key, hashes.SHA256())
    )
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    return key_pem, csr.public_bytes(serialization.Encoding.PEM).decode()


def ca_pem() -> str:
    ensure_ca()
    return _paths()[1].read_text(encoding="utf-8")


def self_signed_cert(host: str, days: int = 825) -> tuple[str, str]:
    """Сертификат для IP-режима (TLS на инбаундах без домена)."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(timezone.utc)
    import ipaddress

    try:
        san = [x509.IPAddress(ipaddress.ip_address(host))]
    except ValueError:
        san = [x509.DNSName(host)]
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=days))
        .add_extension(x509.SubjectAlternativeName(san), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    return key_pem, cert.public_bytes(serialization.Encoding.PEM).decode()
