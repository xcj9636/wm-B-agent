import json
from pathlib import Path
import sys

import pytest

from app.services.media.contracts import AssetScanStatus, MediaAssetKind
from app.services.media.inspection import (
    CommandExecution,
    MediaInspectionRunner,
    ProbeStatus,
    run_bounded_command,
)


class FakeExecutor:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def __call__(self, argv, *, timeout_seconds, max_output_bytes):
        self.calls.append(
            {
                "argv": argv,
                "timeout_seconds": timeout_seconds,
                "max_output_bytes": max_output_bytes,
            }
        )
        return self.results.pop(0)


def execution(*, returncode=0, stdout=b"", stderr=b"", **overrides):
    values = {
        "returncode": returncode,
        "stdout": stdout,
        "stderr": stderr,
        "timed_out": False,
        "output_truncated": False,
    }
    values.update(overrides)
    return CommandExecution(**values)


def runner(executor):
    return MediaInspectionRunner(
        clamscan_path="/usr/bin/clamscan",
        ffprobe_path="/usr/bin/ffprobe",
        timeout_seconds=12,
        max_output_bytes=65_536,
        max_duration_seconds=600,
        max_dimension_pixels=8192,
        executor=executor,
    )


def clean_probe_payload():
    return json.dumps(
        {
            "format": {
                "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
                "duration": "4.250000",
                "size": "4096",
                "tags": {
                    "title": "customer confidential launch",
                    "comment": "do not persist",
                },
            },
            "streams": [
                {
                    "index": 0,
                    "codec_type": "video",
                    "codec_name": "h264",
                    "width": 1920,
                    "height": 1080,
                    "avg_frame_rate": "30/1",
                    "tags": {"handler_name": "private camera name"},
                },
                {
                    "index": 1,
                    "codec_type": "audio",
                    "codec_name": "aac",
                    "sample_rate": "48000",
                    "channels": 2,
                },
            ],
        }
    ).encode()


def test_clean_video_is_scanned_then_probed_with_fixed_argv():
    executor = FakeExecutor(
        [
            execution(stdout=b"ClamAV 1.4.2/27390/Tue Aug 11 00:00:00 2026\n"),
            execution(returncode=0),
            execution(stdout=clean_probe_payload()),
        ]
    )
    path = Path("/private/var/tmp/media-stage/--input.mp4")

    result = runner(executor).inspect(
        path,
        expected_kind=MediaAssetKind.VIDEO,
        expected_mime_type="video/mp4",
        expected_size_bytes=4096,
    )

    assert result.scan_status == AssetScanStatus.PASSED
    assert result.scanner == "clamav"
    assert result.scanner_version == "1.4.2"
    assert result.probe_status == ProbeStatus.PASSED
    assert result.probe is not None
    assert result.probe.duration_seconds == 4.25
    assert result.probe.video_streams[0].width == 1920
    assert result.probe.audio_streams[0].codec == "aac"
    serialized = result.model_dump_json()
    assert "customer confidential" not in serialized
    assert "private camera" not in serialized
    assert str(path) not in serialized

    assert executor.calls[1]["argv"][-2:] == ["--", str(path)]
    assert executor.calls[1]["argv"][0] == "/usr/bin/clamscan"
    assert executor.calls[2]["argv"][0] == "/usr/bin/ffprobe"
    assert executor.calls[2]["argv"][-1] == str(path)
    assert all(isinstance(call["argv"], list) for call in executor.calls)


def test_infected_file_never_reaches_ffprobe_and_path_is_not_persisted():
    executor = FakeExecutor(
        [
            execution(stdout=b"ClamAV 1.4.2/27390\n"),
            execution(
                returncode=1,
                stdout=(
                    b"/private/var/tmp/media-stage/customer.mp4: "
                    b"Eicar-Signature FOUND\n"
                ),
            ),
        ]
    )

    result = runner(executor).inspect(
        Path("/private/var/tmp/media-stage/customer.mp4"),
        expected_kind=MediaAssetKind.VIDEO,
        expected_mime_type="video/mp4",
        expected_size_bytes=4096,
    )

    assert result.scan_status == AssetScanStatus.FAILED
    assert result.signatures == ["Eicar-Signature"]
    assert result.probe_status == ProbeStatus.BLOCKED
    assert len(executor.calls) == 2
    assert "/private/" not in result.model_dump_json()


def test_scanner_timeout_or_truncated_output_is_fail_closed():
    timeout_executor = FakeExecutor(
        [
            execution(stdout=b"ClamAV 1.4.2/27390\n"),
            execution(returncode=-9, timed_out=True),
        ]
    )
    truncated_executor = FakeExecutor(
        [
            execution(stdout=b"ClamAV 1.4.2/27390\n"),
            execution(returncode=0, output_truncated=True),
        ]
    )

    for executor, reason in [
        (timeout_executor, "scanner_timeout"),
        (truncated_executor, "scanner_output_limit"),
    ]:
        result = runner(executor).inspect(
            Path("/tmp/input.png"),
            expected_kind=MediaAssetKind.IMAGE,
            expected_mime_type="image/png",
            expected_size_bytes=1024,
        )
        assert result.scan_status == AssetScanStatus.UNAVAILABLE
        assert result.reason_code == reason
        assert result.probe_status == ProbeStatus.BLOCKED


def test_invalid_probe_json_and_dimension_limit_never_pass():
    malformed = FakeExecutor(
        [
            execution(stdout=b"ClamAV 1.4.2/27390\n"),
            execution(),
            execution(stdout=b"not-json"),
        ]
    )
    oversized_payload = clean_probe_payload().replace(b"1920", b"9000")
    oversized = FakeExecutor(
        [
            execution(stdout=b"ClamAV 1.4.2/27390\n"),
            execution(),
            execution(stdout=oversized_payload),
        ]
    )

    malformed_result = runner(malformed).inspect(
        Path("/tmp/input.mp4"),
        expected_kind=MediaAssetKind.VIDEO,
        expected_mime_type="video/mp4",
        expected_size_bytes=4096,
    )
    oversized_result = runner(oversized).inspect(
        Path("/tmp/input.mp4"),
        expected_kind=MediaAssetKind.VIDEO,
        expected_mime_type="video/mp4",
        expected_size_bytes=4096,
    )

    assert malformed_result.probe_status == ProbeStatus.UNAVAILABLE
    assert malformed_result.reason_code == "probe_invalid_output"
    assert oversized_result.probe_status == ProbeStatus.REJECTED
    assert oversized_result.reason_code == "probe_dimension_limit"


def test_bounded_executor_handles_success_timeout_output_limit_and_missing_binary():
    success = run_bounded_command(
        [sys.executable, "-c", "print('inspection-ok')"],
        timeout_seconds=2,
        max_output_bytes=4096,
    )
    timeout = run_bounded_command(
        [sys.executable, "-c", "import time; time.sleep(5)"],
        timeout_seconds=1,
        max_output_bytes=4096,
    )
    truncated = run_bounded_command(
        [sys.executable, "-c", "print('x' * 100000)"],
        timeout_seconds=2,
        max_output_bytes=1024,
    )
    missing = run_bounded_command(
        ["/definitely-not-installed/b-agent-inspector"],
        timeout_seconds=1,
        max_output_bytes=1024,
    )

    assert success.returncode == 0
    assert success.stdout.strip() == b"inspection-ok"
    assert timeout.timed_out is True
    assert truncated.output_truncated is True
    assert len(truncated.stdout) + len(truncated.stderr) <= 1024
    assert missing.returncode == 127

    with pytest.raises(ValueError, match="absolute"):
        run_bounded_command(
            ["ffprobe", "input.mp4"],
            timeout_seconds=1,
            max_output_bytes=1024,
        )


def test_runner_rejects_unsafe_configuration_and_relative_media_paths():
    with pytest.raises(ValueError, match="absolute"):
        MediaInspectionRunner(
            clamscan_path="clamscan",
            ffprobe_path="/usr/bin/ffprobe",
            timeout_seconds=10,
            max_output_bytes=4096,
            max_duration_seconds=600,
            max_dimension_pixels=8192,
        )

    configured = runner(FakeExecutor([]))
    with pytest.raises(ValueError, match="absolute"):
        configured.inspect(
            Path("relative/input.mp4"),
            expected_kind=MediaAssetKind.VIDEO,
            expected_mime_type="video/mp4",
            expected_size_bytes=4096,
        )
