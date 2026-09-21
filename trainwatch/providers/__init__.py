"""사업자 구현체 레지스트리."""

from __future__ import annotations

from .base import (
    AuthError,
    FakeProvider,
    Provider,
    ProviderError,
    RateLimited,
    SearchRequest,
    SoldOut,
)

__all__ = [
    "AuthError",
    "FakeProvider",
    "Provider",
    "ProviderError",
    "RateLimited",
    "SearchRequest",
    "SoldOut",
    "build_provider",
    "PROVIDERS",
]

PROVIDERS = ("srt", "korail", "fake")


def build_provider(name: str, user_id: str = "", password: str = "", **kwargs) -> Provider:
    """이름으로 사업자 구현체를 만든다. 무거운 import 는 필요할 때만 한다."""

    key = name.strip().lower()
    if key == "srt":
        from .srt import SRTProvider

        return SRTProvider(user_id=user_id, password=password, **kwargs)
    if key in ("korail", "ktx", "letskorail"):
        from .korail import KorailProvider

        return KorailProvider(user_id=user_id, password=password, **kwargs)
    if key in ("fake", "mock", "demo"):
        return FakeProvider(**kwargs)
    raise ValueError(f"알 수 없는 provider: {name!r} (사용 가능: {', '.join(PROVIDERS)})")
