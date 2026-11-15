import hashlib
import hmac
import os

_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_DKLEN = 64
_SALT_BYTES = 16


def _derive(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=_DKLEN)


def hash_password(password: str) -> str:
    salt = os.urandom(_SALT_BYTES)
    return f"{salt.hex()}${_derive(password, salt).hex()}"


def verify_password(password: str, stored: str) -> bool:
    salt_hex, digest_hex = stored.split("$")
    return hmac.compare_digest(_derive(password, bytes.fromhex(salt_hex)), bytes.fromhex(digest_hex))
