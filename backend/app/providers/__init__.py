"""云适配层工厂：根据 provider 名称返回对应实现。"""
from __future__ import annotations

from ..core.crypto import decrypt_secret
from ..models.models import CloudProvider
from .aliyun import AliyunProvider
from .base import BaseProvider
from .volcengine import VolcengineProvider

_REGISTRY: dict[str, type[BaseProvider]] = {
    CloudProvider.ALIBABA.value: AliyunProvider,
    CloudProvider.VOLCENGINE.value: VolcengineProvider,
}

PROVIDER_LABELS = {
    CloudProvider.ALIBABA.value: "阿里云",
    CloudProvider.VOLCENGINE.value: "火山引擎",
}


def get_provider(account) -> BaseProvider:
    """由 CloudAccount ORM 对象构造 Provider（自动解密 SK）。"""
    cls = _REGISTRY.get(account.provider)
    if cls is None:
        raise ValueError(f"不支持的云厂商: {account.provider}")

    return cls(
        access_key_id=account.access_key_id,
        access_key_secret=decrypt_secret(account.access_key_secret_enc),
        region=account.region,
        vpc_ids=account.vpc_ids,
    )


def supported_providers() -> list[dict]:
    return [
        {"value": k, "label": PROVIDER_LABELS.get(k, k)} for k in _REGISTRY
    ]
