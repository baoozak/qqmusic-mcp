from __future__ import annotations

import ctypes
import os
import tempfile
from ctypes import wintypes
from pathlib import Path


CRYPTPROTECT_UI_FORBIDDEN = 0x1


class DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _blob(data: bytes) -> tuple[DataBlob, ctypes.Array[ctypes.c_char]]:
    buffer = ctypes.create_string_buffer(data)
    return DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))), buffer


def _crypt(data: bytes, *, decrypt: bool) -> bytes:
    if os.name != "nt":
        raise RuntimeError("encrypted QQ Music session caching requires Windows")
    source, source_buffer = _blob(data)
    output = DataBlob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    if decrypt:
        ok = crypt32.CryptUnprotectData(
            ctypes.byref(source), None, None, None, None, CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(output)
        )
    else:
        ok = crypt32.CryptProtectData(
            ctypes.byref(source), "QQ Music Organizer session", None, None, None,
            CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(output),
        )
    del source_buffer
    if not ok:
        raise OSError(ctypes.get_last_error(), "Windows DPAPI operation failed")
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel32.LocalFree(output.pbData)


def save_cookie(path: Path, raw_cookie: str) -> None:
    encrypted = _crypt(raw_cookie.encode("utf-8"), decrypt=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(encrypted)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_cookie(path: Path) -> str | None:
    try:
        encrypted = path.read_bytes()
    except FileNotFoundError:
        return None
    return _crypt(encrypted, decrypt=True).decode("utf-8")


def delete_cookie(path: Path) -> None:
    path.unlink(missing_ok=True)
