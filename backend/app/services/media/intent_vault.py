"""Encrypted, immutable storage for provider-bound media generation intents."""
from __future__ import annotations

import base64
import binascii
import json
import os
import secrets
import stat
from pathlib import Path
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import ValidationError

from app.services.media.contracts import GenerationIntent


_REFERENCE_PREFIX = "vault://media-intents/"
_MAX_KEY_FILE_BYTES = 256
_MAX_CIPHERTEXT_BYTES = 65_536


class MediaIntentVaultUnavailable(RuntimeError):
    """The intent cannot be stored or authenticated safely."""


class MediaIntentVaultConflict(RuntimeError):
    """An immutable attempt identifier was reused for different content."""


class EncryptedMediaIntentVault:
    """AES-GCM vault whose authenticated data binds each blob to its reference."""

    def __init__(self, *, root: Path | str, key_file: Path | str) -> None:
        self._root = Path(root)
        self._key = self._read_key(Path(key_file))
        self._prepare_root()

    def store(self, intent: GenerationIntent) -> str:
        payload_ref = f"{_REFERENCE_PREFIX}{intent.attempt_id}"
        plaintext = json.dumps(
            intent.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        nonce = os.urandom(12)
        ciphertext = nonce + AESGCM(self._key).encrypt(
            nonce,
            plaintext,
            payload_ref.encode("ascii"),
        )
        if len(ciphertext) > _MAX_CIPHERTEXT_BYTES:
            raise MediaIntentVaultUnavailable("Media intent payload is too large")

        filename = self._filename(intent.attempt_id)
        directory = self._open_root()
        temporary = f".{filename}.{secrets.token_hex(8)}.tmp"
        descriptor: int | None = None
        try:
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=directory,
            )
            self._write_all(descriptor, ciphertext)
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = None
            try:
                os.link(
                    temporary,
                    filename,
                    src_dir_fd=directory,
                    dst_dir_fd=directory,
                    follow_symlinks=False,
                )
                os.fsync(directory)
            except FileExistsError:
                existing = self._load_uuid(intent.attempt_id, payload_ref)
                if existing != intent:
                    raise MediaIntentVaultConflict(
                        "Media intent attempt is already bound to other content"
                    )
        except MediaIntentVaultConflict:
            raise
        except (OSError, ValueError) as exc:
            raise MediaIntentVaultUnavailable(
                "Media intent vault is unavailable"
            ) from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
            try:
                os.unlink(temporary, dir_fd=directory)
            except FileNotFoundError:
                pass
            finally:
                os.close(directory)
        return payload_ref

    def load(self, payload_ref: str) -> GenerationIntent:
        attempt_id = self._parse_reference(payload_ref)
        return self._load_uuid(attempt_id, payload_ref)

    def _load_uuid(self, attempt_id: UUID, payload_ref: str) -> GenerationIntent:
        directory = self._open_root()
        no_follow = getattr(os, "O_NOFOLLOW", None)
        if no_follow is None:
            os.close(directory)
            raise MediaIntentVaultUnavailable("Media intent vault is unavailable")
        descriptor: int | None = None
        try:
            descriptor = os.open(
                self._filename(attempt_id),
                os.O_RDONLY | no_follow,
                dir_fd=directory,
            )
            info = os.fstat(descriptor)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_mode & 0o077
                or info.st_size < 29
                or info.st_size > _MAX_CIPHERTEXT_BYTES
            ):
                raise MediaIntentVaultUnavailable(
                    "Media intent vault is unavailable"
                )
            payload = self._read_exact(descriptor, info.st_size)
            nonce, ciphertext = payload[:12], payload[12:]
            plaintext = AESGCM(self._key).decrypt(
                nonce,
                ciphertext,
                payload_ref.encode("ascii"),
            )
            intent = GenerationIntent.model_validate(json.loads(plaintext))
            if intent.attempt_id != attempt_id:
                raise MediaIntentVaultUnavailable(
                    "Media intent vault is unavailable"
                )
            return intent
        except MediaIntentVaultUnavailable:
            raise
        except (
            OSError,
            InvalidTag,
            UnicodeDecodeError,
            json.JSONDecodeError,
            ValidationError,
        ) as exc:
            raise MediaIntentVaultUnavailable(
                "Media intent vault is unavailable"
            ) from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
            os.close(directory)

    def _prepare_root(self) -> None:
        try:
            self._root.mkdir(parents=True, mode=0o700, exist_ok=False)
        except FileExistsError:
            pass
        try:
            info = self._root.lstat()
        except OSError as exc:
            raise MediaIntentVaultUnavailable(
                "Media intent vault is unavailable"
            ) from exc
        if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o077:
            raise MediaIntentVaultUnavailable("Media intent vault is unavailable")

    def _open_root(self) -> int:
        no_follow = getattr(os, "O_NOFOLLOW", None)
        directory_flag = getattr(os, "O_DIRECTORY", None)
        if no_follow is None or directory_flag is None:
            raise MediaIntentVaultUnavailable("Media intent vault is unavailable")
        try:
            descriptor = os.open(
                self._root,
                os.O_RDONLY | directory_flag | no_follow,
            )
            info = os.fstat(descriptor)
            if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o077:
                raise MediaIntentVaultUnavailable(
                    "Media intent vault is unavailable"
                )
            return descriptor
        except MediaIntentVaultUnavailable:
            if "descriptor" in locals():
                os.close(descriptor)
            raise
        except OSError as exc:
            raise MediaIntentVaultUnavailable(
                "Media intent vault is unavailable"
            ) from exc

    @staticmethod
    def _read_key(path: Path) -> bytes:
        no_follow = getattr(os, "O_NOFOLLOW", None)
        if no_follow is None:
            raise MediaIntentVaultUnavailable("Media intent key is unavailable")
        descriptor: int | None = None
        try:
            descriptor = os.open(path, os.O_RDONLY | no_follow)
            info = os.fstat(descriptor)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_mode & 0o077
                or info.st_size < 1
                or info.st_size > _MAX_KEY_FILE_BYTES
            ):
                raise MediaIntentVaultUnavailable(
                    "Media intent key is unavailable"
                )
            encoded = EncryptedMediaIntentVault._read_exact(
                descriptor,
                info.st_size,
            ).strip()
            key = base64.b64decode(encoded, altchars=b"-_", validate=True)
            if len(key) != 32:
                raise MediaIntentVaultUnavailable(
                    "Media intent key is unavailable"
                )
            return key
        except MediaIntentVaultUnavailable:
            raise
        except (OSError, binascii.Error, ValueError) as exc:
            raise MediaIntentVaultUnavailable(
                "Media intent key is unavailable"
            ) from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)

    @staticmethod
    def _parse_reference(payload_ref: str) -> UUID:
        if not isinstance(payload_ref, str) or not payload_ref.startswith(
            _REFERENCE_PREFIX
        ):
            raise MediaIntentVaultUnavailable(
                "Media intent reference is unavailable"
            )
        identifier = payload_ref[len(_REFERENCE_PREFIX) :]
        try:
            attempt_id = UUID(identifier)
        except (ValueError, AttributeError) as exc:
            raise MediaIntentVaultUnavailable(
                "Media intent reference is unavailable"
            ) from exc
        if str(attempt_id) != identifier:
            raise MediaIntentVaultUnavailable(
                "Media intent reference is unavailable"
            )
        return attempt_id

    @staticmethod
    def _filename(attempt_id: UUID) -> str:
        return f"{attempt_id}.intent"

    @staticmethod
    def _write_all(descriptor: int, payload: bytes) -> None:
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise OSError("short vault write")
            offset += written

    @staticmethod
    def _read_exact(descriptor: int, expected: int) -> bytes:
        chunks: list[bytes] = []
        remaining = expected
        while remaining:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                raise OSError("short vault read")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise OSError("vault file changed during read")
        return b"".join(chunks)
