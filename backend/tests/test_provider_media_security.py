import pytest

from app.integrations.provider_media import (
    ProviderMediaURLDenied,
    SafeProviderMediaURLPolicy,
)


class StaticResolver:
    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def __call__(self, host):
        self.calls.append(host)
        return list(self.answers[host])


def policy(answers=None):
    resolver = StaticResolver(
        answers
        or {
            "v3b.fal.media": ["8.8.8.8", "1.1.1.1"],
            "cdn.fal.media": ["8.8.4.4"],
        }
    )
    return SafeProviderMediaURLPolicy(
        allowed_hosts={"*.fal.media"},
        resolver=resolver,
    ), resolver


def test_provider_media_url_requires_https_allowlist_and_public_dns():
    service, resolver = policy()

    approved = service.validate(
        "https://v3b.fal.media/files/output.mp4?token=secret"
    )

    assert approved.host == "v3b.fal.media"
    assert approved.port == 443
    assert approved.approved_ips == frozenset({"8.8.8.8", "1.1.1.1"})
    assert resolver.calls == ["v3b.fal.media"]


@pytest.mark.parametrize(
    "url",
    [
        "http://v3b.fal.media/file.mp4",
        "https://v3b.fal.media:8443/file.mp4",
        "https://user:password@v3b.fal.media/file.mp4",
        "https://evil.example/file.mp4",
        "https://fal.media.evil.example/file.mp4",
        "https://127.0.0.1/file.mp4",
    ],
)
def test_provider_media_url_rejects_unsafe_authority(url):
    service, _ = policy()
    with pytest.raises(ProviderMediaURLDenied):
        service.validate(url)


@pytest.mark.parametrize(
    "address",
    [
        "10.0.0.1",
        "127.0.0.1",
        "169.254.169.254",
        "172.16.0.1",
        "192.168.1.1",
        "::1",
        "fc00::1",
        "fe80::1",
    ],
)
def test_provider_media_url_rejects_non_public_dns_answers(address):
    service, _ = policy({"v3b.fal.media": ["8.8.8.8", address]})

    with pytest.raises(ProviderMediaURLDenied):
        service.validate("https://v3b.fal.media/file.mp4")


def test_connected_peer_must_match_the_approved_dns_set():
    service, _ = policy()
    approved = service.validate("https://v3b.fal.media/file.mp4")

    service.assert_connected_peer(approved, "8.8.8.8")
    with pytest.raises(ProviderMediaURLDenied):
        service.assert_connected_peer(approved, "8.8.4.4")
    with pytest.raises(ProviderMediaURLDenied):
        service.assert_connected_peer(approved, "169.254.169.254")


def test_every_redirect_target_is_revalidated():
    service, resolver = policy()

    chain = service.validate_redirect_chain(
        [
            "https://v3b.fal.media/request/output",
            "https://cdn.fal.media/file.mp4",
        ],
        max_redirects=2,
    )

    assert [item.host for item in chain] == [
        "v3b.fal.media",
        "cdn.fal.media",
    ]
    assert resolver.calls == ["v3b.fal.media", "cdn.fal.media"]

    with pytest.raises(ProviderMediaURLDenied):
        service.validate_redirect_chain(
            [
                "https://v3b.fal.media/request/output",
                "https://evil.example/file.mp4",
            ],
            max_redirects=2,
        )

    with pytest.raises(ProviderMediaURLDenied):
        service.validate_redirect_chain(
            [
                "https://v3b.fal.media/1",
                "https://cdn.fal.media/2",
                "https://v3b.fal.media/3",
                "https://cdn.fal.media/4",
            ],
            max_redirects=2,
        )
