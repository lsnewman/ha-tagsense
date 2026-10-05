"""Signed access codes: formats, signing and parsing. Pure functions, no state.

Both formats use only Base32 characters (RFC 4648: A-Z, 2-7), which are in the
QR alphanumeric set, so they fit QR version 2 at error correction M (25
modules, up to 38 characters) and read at doorbell resolution.

  rotating (23):  "TR" + v + handle(4) + mac(16)
                  mac = HMAC-SHA256(person secret, "TR|v|handle|step")[:10]
                  step = floor(unix_time / period); the step is not in the
                  payload: the verifier tries the current and previous step.
  static (34):    "TS" + v + code_id(8) + expiry(7) + mac(16)
                  mac = HMAC-SHA256(static key, "TS|v|code_id|expiry")[:10]
                  expiry = minutes since 2025-01-01 UTC.

`v` is a key-version character: a person's secret version for rotating codes
(re-enrolling bumps it), the static key's version for static codes (rotating
the key bumps it and so invalidates every static code). The MAC is 80 bits.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass

import cv2
import numpy as np

B32 = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
EPOCH = 1735689600                    # 2025-01-01T00:00:00Z
MAC_BYTES = 10                        # 80 bits -> 16 Base32 characters
ROTATING_LEN = 2 + 1 + 4 + 16
STATIC_LEN = 2 + 1 + 8 + 7 + 16
_RE = re.compile(f"^[{B32}]+$")


def b32(data: bytes) -> str:
    return base64.b32encode(data).decode().rstrip("=")


def int_b32(n: int, width: int) -> str:
    out = []
    for _ in range(width):
        out.append(B32[n & 31])
        n >>= 5
    if n:
        raise ValueError("number too large for its field")
    return "".join(reversed(out))


def b32_int(s: str) -> int:
    n = 0
    for c in s:
        n = n * 32 + B32.index(c)
    return n


def mac(key: bytes, message: str) -> str:
    return b32(hmac.new(key, message.encode(), hashlib.sha256).digest()[:MAC_BYTES])


def next_version(v: str) -> str:
    return B32[(B32.index(v) + 1) % 32]


def new_secret(nbytes: int = 20) -> bytes:
    return secrets.token_bytes(nbytes)


def new_id(chars: int) -> str:
    return "".join(secrets.choice(B32) for _ in range(chars))


def minutes(unix: float) -> int:
    return int((unix - EPOCH) // 60)


def unix_of(minute: int) -> int:
    return EPOCH + minute * 60


# --- rotating -----------------------------------------------------------------

def rotating_mac(secret: bytes, v: str, handle: str, step: int) -> str:
    return mac(secret, f"TR|{v}|{handle}|{step}")


def rotating_code(secret: bytes, v: str, handle: str, step: int) -> str:
    return f"TR{v}{handle}{rotating_mac(secret, v, handle, step)}"


# --- static ---------------------------------------------------------------------

def static_mac(key: bytes, v: str, code_id: str, expiry_min: int) -> str:
    return mac(key, f"TS|{v}|{code_id}|{int_b32(expiry_min, 7)}")


def static_code(key: bytes, v: str, code_id: str, expiry_min: int) -> str:
    return f"TS{v}{code_id}{int_b32(expiry_min, 7)}{static_mac(key, v, code_id, expiry_min)}"


# --- parsing ----------------------------------------------------------------------

@dataclass(frozen=True)
class Rotating:
    v: str
    handle: str
    mac: str


@dataclass(frozen=True)
class Static:
    v: str
    code_id: str
    expiry_min: int
    mac: str


def looks_like_ours(text: str) -> bool:
    """A TagSense code by its shape (not yet checked). Anything else is a
    foreign QR code, such as a parcel label."""
    return parse(text) is not None


def parse(text: str) -> Rotating | Static | None:
    t = text.strip()
    if not _RE.match(t):
        return None
    if t.startswith("TR") and len(t) == ROTATING_LEN:
        return Rotating(t[2], t[3:7], t[7:])
    if t.startswith("TS") and len(t) == STATIC_LEN:
        return Static(t[2], t[3:11], b32_int(t[11:18]), t[18:])
    return None


# --- QR image ----------------------------------------------------------------------

def qr_png(payload: str, module_px: int = 12) -> bytes:
    """PNG of the code: error correction M, at least a 4-module quiet zone, large
    modules, so it survives being sent on as a picture."""
    params = cv2.QRCodeEncoder_Params()
    params.correction_level = cv2.QRCodeEncoder_CORRECT_LEVEL_M
    q = cv2.QRCodeEncoder.create(params).encode(payload)
    q = cv2.copyMakeBorder(q, 2, 2, 2, 2, cv2.BORDER_CONSTANT, value=255)
    img = cv2.resize(q, None, fx=module_px, fy=module_px, interpolation=cv2.INTER_NEAREST)
    ok, buf = cv2.imencode(".png", img)
    return buf.tobytes()


def qr_modules(payload: str) -> int:
    """Modules across the symbol itself (21 = version 1, 25 = version 2)."""
    params = cv2.QRCodeEncoder_Params()
    params.correction_level = cv2.QRCodeEncoder_CORRECT_LEVEL_M
    q = cv2.QRCodeEncoder.create(params).encode(payload)
    dark = np.argwhere(q < 128)
    return int(dark[:, 0].max() - dark[:, 0].min() + 1)
