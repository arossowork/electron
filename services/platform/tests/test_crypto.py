"""Envelope encryption and log redaction: no database needed."""

import base64
import logging
import uuid

import pytest

from hwplatform.crypto import CryptoError, Keyring, MasterKeyNotConfigured
from hwplatform.logredact import install_log_redaction, redact
from hwplatform.settings import Settings

KEY = "sk-ant-api03-" + "A1b2C3d4" * 6 + "WXYZ"


def b64(c: bytes) -> str:
    return base64.b64encode(c * 32).decode()


def test_roundtrip_and_fresh_dek_per_key():
    ring = Keyring({1: b"a" * 32})
    t = uuid.uuid4()
    s1, s2 = ring.encrypt(KEY, t, "anthropic"), ring.encrypt(KEY, t, "anthropic")
    assert ring.decrypt(s1, t, "anthropic") == KEY
    assert KEY.encode() not in s1.ciphertext + s1.wrapped_dek
    assert s1.wrapped_dek != s2.wrapped_dek and s1.ciphertext != s2.ciphertext  # random DEK and nonce


def test_ciphertext_copied_to_another_tenant_or_provider_fails():
    ring = Keyring({1: b"a" * 32})
    a, b = uuid.uuid4(), uuid.uuid4()
    sealed = ring.encrypt(KEY, a, "anthropic")
    with pytest.raises(CryptoError):
        ring.decrypt(sealed, b, "anthropic")
    with pytest.raises(CryptoError):
        ring.decrypt(sealed, a, "openai")


def test_tampering_is_detected():
    ring = Keyring({1: b"a" * 32})
    t = uuid.uuid4()
    s = ring.encrypt(KEY, t, "anthropic")
    flipped = bytes([s.ciphertext[0] ^ 1]) + s.ciphertext[1:]
    with pytest.raises(CryptoError):
        ring.decrypt(type(s)(flipped, s.nonce, s.wrapped_dek, s.key_version), t, "anthropic")


def test_master_key_rotation_rewrap_keeps_secret_and_needs_new_key():
    old, new = b"o" * 32, b"n" * 32
    t = uuid.uuid4()
    sealed = Keyring({1: old}).encrypt(KEY, t, "anthropic")
    assert sealed.key_version == 1
    ring = Keyring({1: old, 2: new})
    assert ring.newest == 2
    assert ring.decrypt(sealed, t, "anthropic") == KEY  # any version decrypts
    wrapped, ver = ring.rewrap(sealed.wrapped_dek, sealed.key_version, t, "anthropic")
    assert ver == 2 and wrapped != sealed.wrapped_dek
    rotated = type(sealed)(sealed.ciphertext, sealed.nonce, wrapped, ver)
    assert Keyring({2: new}).decrypt(rotated, t, "anthropic") == KEY  # old key no longer needed
    with pytest.raises(CryptoError):
        Keyring({1: old}).decrypt(rotated, t, "anthropic")
    assert ring.encrypt(KEY, t, "anthropic").key_version == 2  # newest encrypts


def test_keyring_from_settings():
    r = Keyring.from_settings(Settings(master_key=b64(b"a")))
    assert r.versions == [1]
    r = Keyring.from_settings(Settings(master_keys=f"2:{b64(b'b')}, 3:{b64(b'c')}", master_key=b64(b"a")))
    assert r.versions == [1, 2, 3] and r.newest == 3
    with pytest.raises(MasterKeyNotConfigured):
        Keyring.from_settings(Settings())
    with pytest.raises(ValueError, match="32 bytes"):
        Keyring.from_settings(Settings(master_key=base64.b64encode(b"short").decode()))
    with pytest.raises(ValueError, match="conflicts"):
        Keyring.from_settings(Settings(master_keys=f"1:{b64(b'b')}", master_key=b64(b"a")))


def test_redact_function():
    assert KEY not in redact(f"key={KEY} and again {KEY}")
    assert redact("sk-ant-api03-abcdef12345") == "sk-ant-***"
    assert redact("nothing here") == "nothing here"


def test_logging_never_contains_a_key(caplog):
    install_log_redaction()
    log = logging.getLogger("hwplatform.test")
    with caplog.at_level(logging.DEBUG):
        log.info("plain %s", KEY)
        log.warning("dict %r", {"x-api-key": KEY})
        log.info(f"fstring {KEY}")  # noqa: G004
        try:
            raise RuntimeError(f"boom {KEY}")
        except RuntimeError:
            log.exception("failed")
    text = caplog.text + "".join(r.getMessage() for r in caplog.records)
    assert KEY not in text and "sk-ant-***" in text
    assert all(KEY not in (r.exc_text or "") for r in caplog.records)
