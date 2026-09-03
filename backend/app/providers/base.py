"""云适配层统一接口。

上层（同步服务 / 执行器）只面向本模块编程，不感知具体云厂商与资源类型差异。
新增云厂商或资源类型时，只需新增一个实现，上层零改动。
"""
from __future__ import annotations

import abc
import re
from dataclasses import dataclass, field, asdict


@dataclass
class CloudResource:
    """统一的资源视图（同步阶段产出）。"""
    resource_id: str
    resource_name: str
    resource_type: str                  # ECS | RDS
    status: str = "unknown"             # 云上原始状态，统一转小写
    region: str = ""
    zone: str = ""
    spec: str = ""
    charge_type: str = ""               # PostPaid(按量) | PrePaid(包年包月)
    private_ip: str = ""
    vpc_id: str = ""
    tags: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


class ProviderError(Exception):
    """云 API 调用失败的归一化异常。"""

    def __init__(self, message: str, code: str = "", request_id: str = ""):
        self.code = code
        self.request_id = request_id
        super().__init__(message)


class BaseProvider(abc.ABC):
    """云厂商能力抽象。"""

    provider_name: str = ""

    def __init__(self, access_key_id: str, access_key_secret: str, region: str,
                 vpc_ids: list[str] | None = None):
        self.ak = access_key_id
        self.sk = access_key_secret
        self.region = region
        self.vpc_ids = [v for v in (vpc_ids or []) if v]

    # ---------------- 必须实现 ----------------
    @abc.abstractmethod
    def test_connection(self) -> tuple[bool, str]:
        """接入自检：返回 (是否成功, 说明信息)。"""

    @abc.abstractmethod
    def list_ecs(self) -> list[CloudResource]:
        """拉取 ECS 实例清单。"""

    @abc.abstractmethod
    def list_rds(self) -> list[CloudResource]:
        """拉取 RDS 实例清单。"""

    @abc.abstractmethod
    def start_ecs(self, instance_id: str) -> str:
        """启动 ECS，返回 request_id。"""

    @abc.abstractmethod
    def stop_ecs(self, instance_id: str, force: bool = False,
                 stopped_mode: str = "KeepCharging") -> str:
        """停止 ECS，返回 request_id。

        stopped_mode:
          KeepCharging 普通停机（保留实例与库存，随时可开，费用照收）
          StopCharging 节省停机（回收计算资源，省钱，但开机可能因库存不足失败）
        """

    @abc.abstractmethod
    def start_rds(self, instance_id: str) -> str:
        """启动 RDS 实例，返回 request_id。"""

    @abc.abstractmethod
    def stop_rds(self, instance_id: str) -> str:
        """停止 RDS 实例（阿里云称暂停），返回 request_id。"""

    # ---------------- 通用能力 ----------------
    def list_all(self) -> list[CloudResource]:
        """拉取 ECS + RDS；单类失败不影响另一类。"""
        out: list[CloudResource] = []
        errors: list[str] = []
        for fn, label in ((self.list_ecs, "ECS"), (self.list_rds, "RDS")):
            try:
                out.extend(fn())
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{label}: {exc}")
        if errors and not out:
            raise ProviderError("; ".join(errors))
        return out

    def _match_vpc(self, vpc_id: str) -> bool:
        """若账号配置了 VPC 白名单，则只纳管命中 VPC 的资源。"""
        if not self.vpc_ids:
            return True
        return vpc_id in self.vpc_ids

    @staticmethod
    def normalize_env(resource_name: str) -> str:
        """从实例名提取环境标识（STG/DEV/UAT/TEST/PROD...），无则返回空串。"""
        if not resource_name:
            return ""
        m = re.search(r"-(STG|DEV|UAT|SIT|TEST|PROD|QA|PRD)(?:-|$)", resource_name, re.IGNORECASE)
        return m.group(1).upper() if m else ""
