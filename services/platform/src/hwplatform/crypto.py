"""Envelope encryption for tenant secrets (BYOK keys, #14).

Each secret gets a random 256-bit data key (DEK). The secret is sealed with AES-256-GCM under the DEK;
the DEK is wrapped (AES-256-GCM) by a master key (KEK) from the keyring. The database holds only
ciphertext, nonce and the wrapped DEK; the KEKs live in the environment (k8s Secret).

Associated data is `tenant_id:provider` for both seals, so a row copied to another tenant (or provider)
fails authentication on decrypt. The keyring is versioned: the highest version encrypts, every version
decrypts. Rotating the master key = add a new version, run `hwplatform-rotate-master` (re-wraps the
DEKs, secrets untouched), then drop the old version.
"""

from __future__ import annotations

import base64
import binascii
import os
import uuid
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .settings import Settings

_NONCE = 12


class CryptoError(Exception):
    """Decryption failed: wrong tenant/provider, tampered data, or an unknown master key version."""


class MasterKeyNotConfigured(Exception):
    pass


def associated_data(tenant_id: uuid.UUID | str, provider: str) -> bytes:
    return f"{tenant_id}:{provider}".encode()


@dataclass(frozen=True)
class Sealed:
    ciphertext: bytes
    nonce: bytes
    wrapped_dek: bytes
    key_version: int


def _decode_key(b64: str, what: str) -> bytes:
    try:
        raw = base64.b64decode(b64.strip(), validate=True)
    except (binascii.Error, ValueError) as e:
        raise ValueError(f"{what} is not valid base64") from e
    if len(raw) != 32:
        raise ValueError(f"{what} must decode to 32 bytes (got {len(raw)})")
    return raw


class Keyring:
    def __init__(self, keys: dict[int, bytes]):
        if not keys:
            raise MasterKeyNotConfigured("no master key configured (HWPLATFORM_MASTER_KEY / _KEYS)")
        self._keys = dict(keys)
        self.newest = max(self._keys)

    @classmethod
    def from_settings(cls, s: Settings) -> Keyring:
        keys: dict[int, bytes] = {}
        listed = s.master_keys.get_secret_value().strip()
        for part in filter(None, (p.strip() for p in listed.split(","))):
            ver, _, b64 = part.partition(":")
            if not ver.isdigit() or int(ver) < 1 or not b64:
                raise ValueError("HWPLATFORM_MASTER_KEYS must look like '2:<base64>,1:<base64>'")
            keys[int(ver)] = _decode_key(b64, f"master key version {ver}")
        single = s.master_key.get_secret_value().strip()
        if single:
            k = _decode_key(single, "HWPLATFORM_MASTER_KEY")
            if keys.get(1, k) != k:
                raise ValueError("HWPLATFORM_MASTER_KEY (version 1) conflicts with HWPLATFORM_MASTER_KEYS")
            keys[1] = k
        return cls(keys)

    @property
    def versions(self) -> list[int]:
        return sorted(self._keys)

    def _kek(self, version: int) -> AESGCM:
        try:
            return AESGCM(self._keys[version])
        except KeyError:
            raise CryptoError(f"master key version {version} is not in the keyring") from None

    def wrap(self, dek: bytes, version: int, ad: bytes) -> bytes:
        nonce = os.urandom(_NONCE)
        return nonce + self._kek(version).encrypt(nonce, dek, ad)

    def unwrap(self, wrapped: bytes, version: int, ad: bytes) -> bytes:
        try:
            return self._kek(version).decrypt(wrapped[:_NONCE], wrapped[_NONCE:], ad)
        except InvalidTag:
            raise CryptoError("cannot unwrap the data key (wrong tenant or master key)") from None

    def encrypt(self, plaintext: str, tenant_id: uuid.UUID | str, provider: str) -> Sealed:
        ad = associated_data(tenant_id, provider)
        dek = AESGCM.generate_key(bit_length=256)
        nonce = os.urandom(_NONCE)
        ct = AESGCM(dek).encrypt(nonce, plaintext.encode(), ad)
        return Sealed(ct, nonce, self.wrap(dek, self.newest, ad), self.newest)

    def decrypt(self, sealed: Sealed, tenant_id: uuid.UUID | str, provider: str) -> str:
        ad = associated_data(tenant_id, provider)
        dek = self.unwrap(sealed.wrapped_dek, sealed.key_version, ad)
        try:
            return AESGCM(dek).decrypt(sealed.nonce, sealed.ciphertext, ad).decode()
        except InvalidTag:
            raise CryptoError("cannot decrypt the secret (wrong tenant or tampered data)") from None

    def rewrap(
        self, wrapped: bytes, version: int, tenant_id: uuid.UUID | str, provider: str
    ) -> tuple[bytes, int]:
        """Re-wrap a DEK under the newest master key. The sealed secret itself is unchanged."""
        ad = associated_data(tenant_id, provider)
        return self.wrap(self.unwrap(wrapped, version, ad), self.newest, ad), self.newest
