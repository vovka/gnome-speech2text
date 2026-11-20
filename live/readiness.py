"""Fail-closed operator attestation validation; never probes CUDA or a server."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlsplit


def loopback_origin(endpoint: str) -> str | None:
    try:
        parsed, port = urlsplit(endpoint), urlsplit(endpoint).port
    except ValueError:
        return None
    if (parsed.scheme != "http" or parsed.username is not None or parsed.password is not None
            or parsed.hostname not in {"127.0.0.1", "::1"} or port is None or port == 0 or port == 8080
            or parsed.path or parsed.query or parsed.fragment):
        return None
    host = f"[{parsed.hostname}]" if parsed.hostname == "::1" else parsed.hostname
    return f"http://{host}:{port}"


@dataclass(frozen=True)
class GpuAttestation:
    endpoint: str
    model: str
    process_identity: str
    process_started: str
    cuda_verified: bool

    def validate(self, endpoint: str, model: str, identity_reader: Callable[[], tuple[str, str]]) -> tuple[bool, str]:
        origin = loopback_origin(endpoint)
        if origin is None:
            return False, "endpoint is not an exact dedicated literal loopback origin"
        if not self.cuda_verified:
            return False, "CUDA is not operator-attested"
        try:
            identity, started = identity_reader()
        except Exception:
            return False, "server identity cannot be re-verified"
        if (self.endpoint, self.model, self.process_identity, self.process_started) != (origin, model, identity, started):
            return False, "CUDA attestation is missing, stale, or mismatched"
        return True, "GPU path attested; runtime verification remains required"
