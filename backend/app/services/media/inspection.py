"""Fail-closed malware scanning and allowlisted media probing."""

from enum import Enum
import json
import math
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import time
from typing import Callable, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.services.media.contracts import AssetScanStatus, MediaAssetKind


class ProbeStatus(str, Enum):
    PENDING = "pending"
    PASSED = "passed"
    REJECTED = "rejected"
    UNAVAILABLE = "unavailable"
    BLOCKED = "blocked"


class CommandExecution(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    returncode: int
    stdout: bytes = b""
    stderr: bytes = b""
    timed_out: bool = False
    output_truncated: bool = False


class VideoStreamProbe(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    codec: str = Field(min_length=1, max_length=50)
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    frame_rate: str = Field(default="0/0", max_length=30)


class AudioStreamProbe(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    codec: str = Field(min_length=1, max_length=50)
    sample_rate: int = Field(default=0, ge=0, le=768_000)
    channels: int = Field(default=0, ge=0, le=64)


class MediaProbe(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    format_name: str = Field(min_length=1, max_length=200)
    duration_seconds: float = Field(ge=0)
    size_bytes: int = Field(ge=1)
    video_streams: list[VideoStreamProbe] = Field(default_factory=list, max_length=16)
    audio_streams: list[AudioStreamProbe] = Field(default_factory=list, max_length=32)


class MediaInspectionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scanner: str = "clamav"
    scanner_version: str = Field(min_length=1, max_length=100)
    scan_status: AssetScanStatus
    signatures: list[str] = Field(default_factory=list, max_length=100)
    probe_status: ProbeStatus
    probe: Optional[MediaProbe] = None
    reason_code: str = Field(min_length=1, max_length=100)


Executor = Callable[..., CommandExecution]


class MediaInspectionRunner:
    """Runs fixed ClamAV/FFprobe commands and emits sanitized evidence."""

    def __init__(
        self,
        *,
        clamscan_path: str,
        ffprobe_path: str,
        timeout_seconds: int,
        max_output_bytes: int,
        max_duration_seconds: int,
        max_dimension_pixels: int,
        executor: Executor = None,
    ) -> None:
        self._clamscan_path = _absolute_executable(clamscan_path)
        self._ffprobe_path = _absolute_executable(ffprobe_path)
        if timeout_seconds < 1:
            raise ValueError("inspection timeout must be positive")
        if max_output_bytes < 1024:
            raise ValueError("inspection output limit is too small")
        self._timeout_seconds = timeout_seconds
        self._max_output_bytes = max_output_bytes
        self._max_duration_seconds = max_duration_seconds
        self._max_dimension_pixels = max_dimension_pixels
        self._executor = executor or run_bounded_command

    def inspect(
        self,
        path: Path,
        *,
        expected_kind: MediaAssetKind,
        expected_mime_type: str,
        expected_size_bytes: int,
    ) -> MediaInspectionResult:
        if not path.is_absolute():
            raise ValueError("inspection path must be absolute")
        scanner_version = self._scanner_version()
        if scanner_version is None:
            return self._unavailable("scanner_version_unavailable")

        scan = self._execute(
            [
                self._clamscan_path,
                "--no-summary",
                "--infected",
                "--stdout",
                f"--max-filesize={expected_size_bytes}",
                f"--max-scansize={expected_size_bytes}",
                "--max-recursion=16",
                "--max-files=1000",
                "--alert-encrypted=yes",
                "--",
                str(path),
            ]
        )
        if scan.timed_out:
            return self._unavailable("scanner_timeout", scanner_version)
        if scan.output_truncated:
            return self._unavailable("scanner_output_limit", scanner_version)
        if scan.returncode == 1:
            return MediaInspectionResult(
                scanner_version=scanner_version,
                scan_status=AssetScanStatus.FAILED,
                signatures=_clamav_signatures(scan.stdout),
                probe_status=ProbeStatus.BLOCKED,
                reason_code="malware_detected",
            )
        if scan.returncode != 0:
            return self._unavailable("scanner_error", scanner_version)

        mime_reason = _kind_mime_reason(expected_kind, expected_mime_type)
        if mime_reason is not None:
            return MediaInspectionResult(
                scanner_version=scanner_version,
                scan_status=AssetScanStatus.PASSED,
                probe_status=ProbeStatus.REJECTED,
                reason_code=mime_reason,
            )
        return self._probe(
            path,
            scanner_version=scanner_version,
            expected_kind=expected_kind,
            expected_size_bytes=expected_size_bytes,
        )

    def _probe(
        self,
        path: Path,
        *,
        scanner_version: str,
        expected_kind: MediaAssetKind,
        expected_size_bytes: int,
    ) -> MediaInspectionResult:
        execution = self._execute(
            [
                self._ffprobe_path,
                "-v",
                "error",
                "-max_alloc",
                "67108864",
                "-cpucount",
                "1",
                "-protocol_whitelist",
                "file,crypto",
                "-probesize",
                "10000000",
                "-analyzeduration",
                "5000000",
                "-show_entries",
                (
                    "format=format_name,duration,size:"
                    "stream=index,codec_type,codec_name,width,height,"
                    "avg_frame_rate,sample_rate,channels"
                ),
                "-of",
                "json",
                str(path),
            ]
        )
        if execution.timed_out:
            return self._probe_failure(scanner_version, "probe_timeout")
        if execution.output_truncated:
            return self._probe_failure(scanner_version, "probe_output_limit")
        if execution.returncode != 0:
            return self._probe_failure(scanner_version, "probe_error")
        try:
            payload = json.loads(execution.stdout.decode("utf-8"))
            probe = _allowlisted_probe(payload)
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            return self._probe_failure(scanner_version, "probe_invalid_output")

        reason = self._probe_rejection_reason(
            probe,
            expected_kind=expected_kind,
            expected_size_bytes=expected_size_bytes,
        )
        if reason is not None:
            return MediaInspectionResult(
                scanner_version=scanner_version,
                scan_status=AssetScanStatus.PASSED,
                probe_status=ProbeStatus.REJECTED,
                probe=probe,
                reason_code=reason,
            )
        return MediaInspectionResult(
            scanner_version=scanner_version,
            scan_status=AssetScanStatus.PASSED,
            probe_status=ProbeStatus.PASSED,
            probe=probe,
            reason_code="inspection_passed",
        )

    def _probe_rejection_reason(
        self,
        probe: MediaProbe,
        *,
        expected_kind: MediaAssetKind,
        expected_size_bytes: int,
    ) -> Optional[str]:
        if probe.size_bytes != expected_size_bytes:
            return "probe_size_mismatch"
        if probe.duration_seconds > self._max_duration_seconds:
            return "probe_duration_limit"
        if any(
            stream.width > self._max_dimension_pixels
            or stream.height > self._max_dimension_pixels
            for stream in probe.video_streams
        ):
            return "probe_dimension_limit"
        if expected_kind in {MediaAssetKind.IMAGE, MediaAssetKind.VIDEO} and not (
            probe.video_streams
        ):
            return "probe_missing_video_stream"
        if expected_kind == MediaAssetKind.AUDIO and not probe.audio_streams:
            return "probe_missing_audio_stream"
        return None

    def _scanner_version(self) -> Optional[str]:
        execution = self._execute([self._clamscan_path, "--version"])
        if (
            execution.returncode != 0
            or execution.timed_out
            or execution.output_truncated
        ):
            return None
        match = re.search(rb"ClamAV\s+([^/\s]+)", execution.stdout)
        return match.group(1).decode("ascii") if match else None

    def _execute(self, argv: list[str]) -> CommandExecution:
        return self._executor(
            argv,
            timeout_seconds=self._timeout_seconds,
            max_output_bytes=self._max_output_bytes,
        )

    @staticmethod
    def _unavailable(
        reason_code: str,
        scanner_version: str = "unavailable",
    ) -> MediaInspectionResult:
        return MediaInspectionResult(
            scanner_version=scanner_version,
            scan_status=AssetScanStatus.UNAVAILABLE,
            probe_status=ProbeStatus.BLOCKED,
            reason_code=reason_code,
        )

    @staticmethod
    def _probe_failure(
        scanner_version: str,
        reason_code: str,
    ) -> MediaInspectionResult:
        return MediaInspectionResult(
            scanner_version=scanner_version,
            scan_status=AssetScanStatus.PASSED,
            probe_status=ProbeStatus.UNAVAILABLE,
            reason_code=reason_code,
        )


def run_bounded_command(
    argv: list[str],
    *,
    timeout_seconds: int,
    max_output_bytes: int,
) -> CommandExecution:
    """Execute without a shell and terminate on time/output limit."""
    if not argv or not os.path.isabs(argv[0]):
        raise ValueError("inspection executable must be an absolute path")
    environment = {
        "PATH": "/usr/bin:/bin",
        "LANG": "C",
        "LC_ALL": "C",
    }
    try:
        process = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd="/",
            env=environment,
            close_fds=True,
            start_new_session=True,
            shell=False,
        )
    except OSError:
        return CommandExecution(returncode=127)

    output = {"stdout": bytearray(), "stderr": bytearray()}
    selector = selectors.DefaultSelector()
    assert process.stdout is not None
    assert process.stderr is not None
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    deadline = time.monotonic() + timeout_seconds
    timed_out = False
    truncated = False
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                _terminate_process_group(process)
                break
            for key, _ in selector.select(timeout=min(remaining, 0.1)):
                chunk = os.read(key.fileobj.fileno(), 8192)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                current_size = len(output["stdout"]) + len(output["stderr"])
                available = max_output_bytes - current_size
                if available <= 0 or len(chunk) > available:
                    if available > 0:
                        output[key.data].extend(chunk[:available])
                    truncated = True
                    _terminate_process_group(process)
                    break
                output[key.data].extend(chunk)
            if truncated:
                break
            if process.poll() is not None and not selector.get_map():
                break
        returncode = process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        timed_out = True
        _terminate_process_group(process)
        returncode = process.wait(timeout=1)
    finally:
        selector.close()
        process.stdout.close()
        process.stderr.close()
    return CommandExecution(
        returncode=returncode,
        stdout=bytes(output["stdout"]),
        stderr=bytes(output["stderr"]),
        timed_out=timed_out,
        output_truncated=truncated,
    )


def _terminate_process_group(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _absolute_executable(value: str) -> str:
    normalized = value.strip()
    if not normalized or not os.path.isabs(normalized):
        raise ValueError("inspection executable must be an absolute path")
    return normalized


def _clamav_signatures(stdout: bytes) -> list[str]:
    signatures = []
    for raw_line in stdout.decode("utf-8", errors="replace").splitlines():
        if not raw_line.endswith(" FOUND") or ": " not in raw_line:
            continue
        signature = raw_line.rsplit(": ", 1)[-1].removesuffix(" FOUND").strip()
        if signature and signature not in signatures:
            signatures.append(signature[:200])
    return signatures[:100]


def _kind_mime_reason(kind: MediaAssetKind, mime_type: str) -> Optional[str]:
    prefix_by_kind = {
        MediaAssetKind.IMAGE: "image/",
        MediaAssetKind.VIDEO: "video/",
        MediaAssetKind.AUDIO: "audio/",
    }
    prefix = prefix_by_kind.get(kind)
    if prefix is not None and not mime_type.strip().lower().startswith(prefix):
        return "probe_kind_mime_mismatch"
    return None


def _allowlisted_probe(payload: object) -> MediaProbe:
    if not isinstance(payload, dict):
        raise ValueError("probe output must be an object")
    format_payload = payload.get("format")
    stream_payloads = payload.get("streams")
    if not isinstance(format_payload, dict) or not isinstance(stream_payloads, list):
        raise ValueError("probe output is incomplete")
    duration = float(format_payload.get("duration", 0))
    if not math.isfinite(duration) or duration < 0:
        raise ValueError("probe duration is invalid")
    size_bytes = int(format_payload["size"])
    format_name = str(format_payload["format_name"]).strip()
    video_streams = []
    audio_streams = []
    for stream in stream_payloads[:64]:
        if not isinstance(stream, dict):
            continue
        codec_type = stream.get("codec_type")
        codec = str(stream.get("codec_name") or "unknown")[:50]
        if codec_type == "video":
            video_streams.append(
                VideoStreamProbe(
                    codec=codec,
                    width=int(stream.get("width") or 0),
                    height=int(stream.get("height") or 0),
                    frame_rate=str(stream.get("avg_frame_rate") or "0/0")[:30],
                )
            )
        elif codec_type == "audio":
            audio_streams.append(
                AudioStreamProbe(
                    codec=codec,
                    sample_rate=int(stream.get("sample_rate") or 0),
                    channels=int(stream.get("channels") or 0),
                )
            )
    return MediaProbe(
        format_name=format_name,
        duration_seconds=duration,
        size_bytes=size_bytes,
        video_streams=video_streams,
        audio_streams=audio_streams,
    )
