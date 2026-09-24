"""Encrypted identity vault — TRD §5 core privacy control.

Maps WhatsApp phone number (PII) <-> anonymous UUID. The mapping is stored
AES-256-GCM encrypted at rest; the plaintext phone number exists only:
  * transiently inside this service during resolve/enrol,
  * never in logs (logging.scrub is a second line of defence),
  * never downstream — every other table/service uses the UUID only.

A blind index (HMAC-SHA256 of the normalised MSISDN under a separate key)
allows lookup-by-phone without decrypting anything.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import uuid as uuid_mod

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_KEY_ID_RE = re.compile(r"^\+?[0-9]{8,15}$")


def normalise_msisdn(raw: str) -> str:
    """Normalise a Kenyan phone number to E.164-ish form for stable hashing.

    '0712345678' / '+254712345678' / '254712345678' all map to '+254712345678'.
    Non-Kenyan numbers are kept in whatever international form they arrive in.
    """
    digits = re.sub(r"[^0-9+]", "", raw.strip())
    if digits.startswith("+"):
        digits = digits[1:]
    if len(digits) == 10 and digits.startswith("0"):
        digits = "254" + digits[1:]
    elif len(digits) == 9 and digits.startswith(("7", "1")):
        digits = "254" + digits
    return "+" + digits


class IdentityVault:
    """Encrypt/decrypt phone<->UUID mappings."""

    def __init__(self, master_key_b64: str, blind_index_salt: str | None = None):
        key = base64.b64decode(master_key_b64)
        if len(key) != 32:
            raise ValueError("vault_master_key must be base64 of 32 bytes (AES-256)")
        self._aesgcm = AESGCM(key)
        # Separate derived key for the blind index so it can't be inverted
        # from the encryption key material by someone with DB-only access.
        salt = (blind_index_salt or "okoa-blind-index-v1").encode()
        self._bi_key = hashlib.pbkdf2_hmac("sha256", key, salt, 100_000)

    # ------------------------------------------------------------------ utils
    @staticmethod
    def new_uuid() -> str:
        return str(uuid_mod.uuid4())

    def blind_index(self, msisdn: str) -> str:
        norm = normalise_msisdn(msisdn)
        return hmac.new(self._bi_key, norm.encode(), hashlib.sha256).hexdigest()

    # ------------------------------------------------------------- primitives
    def encrypt_phone(self, msisdn: str, user_uuid: str) -> tuple[bytes, bytes]:
        """Returns (nonce, ciphertext). Ciphertext binds uuid to phone so a
        row can't be tampered with to swap identities (AAD = uuid)."""
        nonce = os.urandom(12)
        ct = self._aesgcm.encrypt(nonce, normalise_msisdn(msisdn).encode(), user_uuid.encode())
        return nonce, ct

    def decrypt_phone(self, nonce: bytes, ciphertext: bytes, user_uuid: str) -> str:
        return self._aesgcm.decrypt(nonce, ciphertext, user_uuid.encode()).decode()

    # ------------------------------------------------------------ high level
    def enrol(self, phone: str) -> tuple[str, dict]:
        """Create a fresh anonymous identity for a phone number.

        Returns (user_uuid, vault_row_fields) — caller persists the row fields.
        """
        user_uuid = self.new_uuid()
        norm = normalise_msisdn(phone)
        nonce, ct = self.encrypt_phone(norm, user_uuid)
        row = {
            "id": uuid_mod.uuid4().hex,
            "blind_index": self.blind_index(norm),
            "user_uuid": user_uuid,
            "cipher_nonce": nonce,
            "ciphertext": ct,
            "status": "active",
        }
        return user_uuid, row

    def resolve(self, row, user_uuid_hint: str | None = None) -> str:
        """Decrypt a vault row back to the phone (only used by the
        data-wipe / legal-hold admin flows — never on the message path)."""
        return self.decrypt_phone(bytes(row["cipher_nonce"]), bytes(row["ciphertext"]), row["user_uuid"])


def generate_key_material() -> str:
    """CLI helper: print a fresh base64 vault key."""
    return base64.b64encode(os.urandom(32)).decode()


# Small JSON envelope used by tests to round-trip rows.
def row_to_json(row: dict) -> str:
    r = dict(row)
    r["cipher_nonce"] = base64.b64encode(row["cipher_nonce"]).decode()
    r["ciphertext"] = base64.b64encode(row["ciphertext"]).decode()
    return json.dumps(r)


def row_from_json(s: str) -> dict:
    r = json.loads(s)
    r["cipher_nonce"] = base64.b64decode(r["cipher_nonce"])
    r["ciphertext"] = base64.b64decode(r["ciphertext"])
    return r
