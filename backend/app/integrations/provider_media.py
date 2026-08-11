"""SSRF-safe authority validation for provider-owned media result URLs."""

import ipaddress
import socket
from typing import Callable, FrozenSet, Iterable, List, Set
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field


class ProviderMediaURLDenied(RuntimeError):
    pass


class ApprovedProviderMediaURL(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    url: str = Field(min_length=1, max_length=8000)
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(ge=1, le=65535)
    approved_ips: FrozenSet[str] = Field(min_length=1)


Resolver = Callable[[str], Iterable[str]]


class SafeProviderMediaURLPolicy:
    """Validate every URL hop and bind it to public DNS answers.

    The downloader must additionally call ``assert_connected_peer`` with the
    actual socket peer address. URL validation alone cannot prevent a resolver
    answer from changing between lookup and connection.
    """

    def __init__(
        self,
        *,
        allowed_hosts: Set[str],
        resolver: Resolver | None = None,
    ) -> None:
        normalized = {self._normalize_pattern(item) for item in allowed_hosts}
        if not normalized:
            raise ValueError("At least one provider media host is required")
        self._allowed_hosts = normalized
        self._resolver = resolver or self._resolve_public_addresses

    def validate(self, url: str) -> ApprovedProviderMediaURL:
        try:
            parsed = urlsplit(url)
            port = parsed.port
        except ValueError as exc:
            raise ProviderMediaURLDenied("Provider media URL is invalid") from exc
        if parsed.scheme.lower() != "https":
            raise ProviderMediaURLDenied("Provider media URL must use HTTPS")
        if parsed.username is not None or parsed.password is not None:
            raise ProviderMediaURLDenied("Provider media URL cannot contain credentials")
        if parsed.fragment:
            raise ProviderMediaURLDenied("Provider media URL cannot contain a fragment")
        host = (parsed.hostname or "").lower().rstrip(".")
        if not host or not self._host_allowed(host):
            raise ProviderMediaURLDenied("Provider media host is not approved")
        if port not in {None, 443}:
            raise ProviderMediaURLDenied("Provider media URL must use port 443")

        try:
            addresses = frozenset(str(item) for item in self._resolver(host))
        except Exception as exc:
            raise ProviderMediaURLDenied(
                "Provider media host could not be resolved"
            ) from exc
        if not addresses:
            raise ProviderMediaURLDenied("Provider media host has no addresses")
        for address in addresses:
            if not self._is_public_ip(address):
                raise ProviderMediaURLDenied(
                    "Provider media host resolved to a non-public address"
                )
        return ApprovedProviderMediaURL(
            url=url,
            host=host,
            port=443,
            approved_ips=addresses,
        )

    def assert_connected_peer(
        self,
        approved: ApprovedProviderMediaURL,
        peer_ip: str,
    ) -> None:
        if not self._is_public_ip(peer_ip):
            raise ProviderMediaURLDenied(
                "Provider media connection used a non-public peer"
            )
        if peer_ip not in approved.approved_ips:
            raise ProviderMediaURLDenied(
                "Provider media peer did not match the approved DNS set"
            )

    def validate_redirect_chain(
        self,
        urls: List[str],
        *,
        max_redirects: int,
    ) -> List[ApprovedProviderMediaURL]:
        if max_redirects < 0:
            raise ValueError("max_redirects cannot be negative")
        if not urls:
            raise ProviderMediaURLDenied("Provider media URL chain is empty")
        if len(urls) - 1 > max_redirects:
            raise ProviderMediaURLDenied("Provider media redirect limit exceeded")
        return [self.validate(url) for url in urls]

    def _host_allowed(self, host: str) -> bool:
        for pattern in self._allowed_hosts:
            if pattern.startswith("*."):
                suffix = pattern[1:]
                if host.endswith(suffix) and host != suffix[1:]:
                    return True
            elif host == pattern:
                return True
        return False

    @staticmethod
    def _normalize_pattern(value: str) -> str:
        pattern = value.strip().lower().rstrip(".")
        if (
            not pattern
            or "://" in pattern
            or "/" in pattern
            or pattern.count("*") > 1
            or ("*" in pattern and not pattern.startswith("*."))
        ):
            raise ValueError("Provider media host pattern is invalid")
        return pattern

    @staticmethod
    def _is_public_ip(value: str) -> bool:
        try:
            return ipaddress.ip_address(value).is_global
        except ValueError:
            return False

    @staticmethod
    def _resolve_public_addresses(host: str) -> Iterable[str]:
        return {
            item[4][0]
            for item in socket.getaddrinfo(
                host,
                443,
                type=socket.SOCK_STREAM,
            )
        }
