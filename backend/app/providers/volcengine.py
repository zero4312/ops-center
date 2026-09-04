"""火山引擎 Provider：ECS + RDS for MySQL。

火山侧不使用 SDK，直接调用官方 OpenAPI 并使用 SigV4 签名（hmac/hashlib 标准库实现）。
签名对服务器时间敏感：机器时间与标准时间偏差 > 15 分钟会导致 401，需开启 NTP。
"""
from __future__ import annotations

import datetime
import hashlib
import hmac
import json
import re
from typing import Any
from urllib.parse import quote

import requests

from .base import BaseProvider, CloudResource, ProviderError, mib_to_gb

TIMEOUT = 30
PAGE_SIZE = 100


class VolcengineProvider(BaseProvider):
    provider_name = "volcengine"

    # ------------------------------------------------------------------
    # SigV4 签名
    # ------------------------------------------------------------------
    @staticmethod
    def _hmac_sha256(key: bytes, msg: str) -> bytes:
        return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

    @classmethod
    def _signing_key(cls, sk: str, date: str, region: str, service: str) -> bytes:
        k = cls._hmac_sha256(sk.encode("utf-8"), date)
        k = cls._hmac_sha256(k, region)
        k = cls._hmac_sha256(k, service)
        k = cls._hmac_sha256(k, "request")
        return k

    @classmethod
    def _sign(cls, host: str, method: str, action: str, version: str, body: str,
              ak: str, sk: str, region: str, service: str) -> dict:
        x_date = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        body_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()

        headers = {
            "Content-Type": "application/json",
            "Host": host,
            "X-Date": x_date,
            "X-Content-Sha256": body_hash,
        }
        signed: dict[str, str] = {}
        for k, v in headers.items():
            if k in ("Content-Type", "Content-Md5", "Host") or k.startswith("X-"):
                signed[k.lower()] = v
        if ":" in signed.get("host", ""):
            h, p = signed["host"].split(":", 1)
            if p in ("80", "443"):
                signed["host"] = h

        signed_str = "".join(f"{k}:{signed[k]}\n" for k in sorted(signed))
        signed_headers = ";".join(sorted(signed.keys()))

        query = {"Action": action, "Version": version}
        canonical_query = "&".join(
            f"{quote(k, safe='-_.~')}={quote(str(v), safe='-_.~')}" for k, v in sorted(query.items())
        )
        canonical_request = "\n".join(
            [method, "/", canonical_query, signed_str, signed_headers, body_hash]
        )
        credential_scope = "/".join([x_date[:8], region, service, "request"])
        string_to_sign = "\n".join([
            "HMAC-SHA256",
            x_date,
            credential_scope,
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        ])
        key = cls._signing_key(sk, x_date[:8], region, service)
        signature = hmac.new(key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

        headers["Authorization"] = (
            f"HMAC-SHA256 Credential={ak}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        )
        return headers

    def _call(self, service: str, action: str, version: str, payload: dict) -> dict:
        host = f"{service}.{self.region}.volcengineapi.com"
        body = json.dumps(payload, ensure_ascii=False)
        headers = self._sign(host, "POST", action, version, body,
                             self.ak, self.sk, self.region, service)
        url = f"https://{host}/?Action={action}&Version={version}"
        try:
            resp = requests.post(url, headers=headers, data=body, timeout=TIMEOUT)
        except requests.RequestException as exc:
            raise ProviderError(f"网络请求失败: {exc}") from exc

        try:
            data = resp.json()
        except ValueError as exc:
            raise ProviderError(f"响应非 JSON（HTTP {resp.status_code}）: {resp.text[:200]}") from exc

        err = (data.get("ResponseMetadata") or {}).get("Error")
        if err:
            raise ProviderError(
                f"{err.get('Message') or err.get('MessageCN') or '未知错误'}",
                code=str(err.get("Code") or ""),
                request_id=str((data.get("ResponseMetadata") or {}).get("RequestId") or ""),
            )
        if resp.status_code >= 400:
            raise ProviderError(f"HTTP {resp.status_code}: {resp.text[:200]}")
        return data.get("Result") or {}

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    def test_connection(self) -> tuple[bool, str]:
        try:
            result = self._call("ecs", "DescribeInstances", "2020-04-01",
                                {"RegionId": self.region, "MaxResults": 1})
            total = result.get("TotalCount", "?")
            return True, f"连接正常（{self.region} 可见 ECS {total} 台）"
        except ProviderError as exc:
            return False, str(exc)

    def list_ecs(self) -> list[CloudResource]:
        out: list[CloudResource] = []
        payload: dict[str, Any] = {"RegionId": self.region, "MaxResults": PAGE_SIZE}
        while True:
            result = self._call("ecs", "DescribeInstances", "2020-04-01", payload)
            insts = result.get("Instances") or []
            for it in insts:
                vpc = it.get("VpcId") or ""
                if not self._match_vpc(vpc):
                    continue
                out.append(CloudResource(
                    resource_id=it.get("InstanceId", ""),
                    resource_name=it.get("InstanceName") or it.get("InstanceId", ""),
                    resource_type="ECS",
                    status=(it.get("Status") or "unknown").lower(),
                    region=self.region,
                    zone=it.get("ZoneId", ""),
                    spec=it.get("InstanceTypeId") or it.get("InstanceType") or "",
                    cpu=self._to_int(it.get("Cpus") if it.get("Cpus") is not None else it.get("Cpu")),
                    memory_gb=mib_to_gb(it.get("MemorySize")),
                    charge_type=self._norm_charge(it.get("InstanceChargeType", "")),
                    private_ip=self._extract_ip(it),
                    vpc_id=vpc,
                ))
            token = result.get("NextToken")
            if not token or not insts:
                break
            payload = {"RegionId": self.region, "MaxResults": PAGE_SIZE, "NextToken": token}
        return out

    def list_rds(self) -> list[CloudResource]:
        out: list[CloudResource] = []
        page = 1
        while True:
            result = self._call("rds_mysql", "DescribeDBInstances", "2022-01-01",
                                {"RegionId": self.region, "PageSize": PAGE_SIZE, "PageNumber": page})
            items = result.get("Instances") or []
            for db in items:
                vpc = db.get("VpcId") or ""
                if not self._match_vpc(vpc):
                    continue
                engine = db.get("Engine", "")
                ver = db.get("EngineVersion", "")
                cpu, mem_gb = self._parse_rds_specs(db)
                out.append(CloudResource(
                    resource_id=db.get("InstanceId", ""),
                    resource_name=db.get("InstanceName") or db.get("InstanceId", ""),
                    resource_type="RDS",
                    status=(db.get("InstanceStatus") or "unknown").lower(),
                    region=self.region,
                    zone=db.get("ZoneId") or db.get("Zone", ""),
                    spec=db.get("NodeSpec") or db.get("DBInstanceClass", ""),
                    engine_version=f"{engine} {ver}".strip(),
                    cpu=cpu,
                    memory_gb=mem_gb,
                    charge_type=self._norm_charge(db.get("ChargeType", "")),
                    private_ip=self._extract_endpoint(db),
                    vpc_id=vpc,
                ))
            if len(items) < PAGE_SIZE:
                break
            page += 1
        return out

    # ------------------------------------------------------------------
    # 操作
    # ------------------------------------------------------------------
    def start_ecs(self, instance_id: str) -> str:
        result = self._call("ecs", "StartInstance", "2020-04-01",
                            {"InstanceId": instance_id})
        return (result.get("RequestId") or
                ((result.get("ResponseMetadata") or {}).get("RequestId")) or "")

    def stop_ecs(self, instance_id: str, force: bool = False,
                 stopped_mode: str = "StopCharging") -> str:
        payload: dict[str, Any] = {"InstanceId": instance_id, "StoppedMode": stopped_mode}
        if force:
            payload["ForceStop"] = True
        result = self._call("ecs", "StopInstance", "2020-04-01", payload)
        return (result.get("RequestId") or
                ((result.get("ResponseMetadata") or {}).get("RequestId")) or "")

    def start_rds(self, instance_id: str) -> str:
        result = self._call("rds_mysql", "StartDBInstance", "2022-01-01",
                            {"InstanceId": instance_id})
        return (result.get("RequestId") or
                ((result.get("ResponseMetadata") or {}).get("RequestId")) or "")

    def stop_rds(self, instance_id: str) -> str:
        result = self._call("rds_mysql", "StopDBInstance", "2022-01-01",
                            {"InstanceId": instance_id})
        return (result.get("RequestId") or
                ((result.get("ResponseMetadata") or {}).get("RequestId")) or "")

    # ------------------------------------------------------------------
    # 工具
    # ------------------------------------------------------------------
    _NODE_SPEC_RE = re.compile(r"(\d+)c(\d+)g(?:\.|$)", re.IGNORECASE)

    @classmethod
    def _parse_rds_specs(cls, db: dict) -> tuple[int | None, int | None]:
        """从 RDS 实例数据换算 CPU 核数与内存 GB。

        优先解析 NodeSpec（如 rds.mysql.1c2g -> 1 核 2GB）；
        其次读取 vCPU / Memory 直出字段（Memory 单位按 GB/MB 自适应）。
        """
        node_spec = str(db.get("NodeSpec") or "")
        m = cls._NODE_SPEC_RE.search(node_spec)
        if m:
            return int(m.group(1)), int(m.group(2))

        cpu = db.get("VCpu") or db.get("Vcpu") or db.get("Cpu")
        mem = db.get("Memory") or db.get("MemorySize")
        cpu = cls._to_int(cpu)
        mem = cls._to_int(mem)
        if mem is not None and mem >= 1024:      # 数值过大按 MB 处理
            mem = max(1, round(mem / 1024))
        return cpu, mem

    @staticmethod
    def _to_int(v) -> int | None:
        """宽容地把云 API 返回值转为 int，失败返回 None。"""
        if v is None:
            return None
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _norm_charge(raw: str) -> str:
        low = (raw or "").lower()
        if low in ("postpaid", "post_paid", "postpay", "按量计费"):
            return "PostPaid"
        if low in ("prepaid", "pre_paid", "prepay", "包年包月"):
            return "PrePaid"
        return raw or ""

    @staticmethod
    def _extract_ip(inst: dict) -> str:
        nics = (inst.get("NetworkInterfaces") or [])
        for nic in nics:
            if nic.get("PrimaryIpAddress"):
                return nic["PrimaryIpAddress"]
        return inst.get("PrimaryIpAddress") or inst.get("PrivateIpAddress") or ""

    @staticmethod
    def _extract_endpoint(db: dict) -> str:
        """取 RDS 连接地址（内网域名优先）。"""
        eps = db.get("Endpoints") or []
        for ep in eps:
            if (ep.get("NetworkType") or "").lower() in ("private", "inner"):
                return ep.get("DomainName") or ep.get("Address") or ""
        if eps:
            return eps[0].get("DomainName") or eps[0].get("Address") or ""
        return db.get("ConnectionString") or db.get("Endpoint") or ""
