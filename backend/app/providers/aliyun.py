"""阿里云 Provider：ECS + RDS（华东/香港等多地域）。

使用官方 aliyun-python-sdk（core / ecs / rds），多账号各自实例化一个 Provider。
"""
from __future__ import annotations

import json

from aliyunsdkcore.client import AcsClient
from aliyunsdkcore.acs_exception.exceptions import ClientException, ServerException
from aliyunsdkecs.request.v20140526 import DescribeInstancesRequest
from aliyunsdkecs.request.v20140526 import StartInstancesRequest, StopInstancesRequest
from aliyunsdkrds.request.v20140815 import DescribeDBInstancesRequest
from aliyunsdkrds.request.v20140815 import StartDBInstanceRequest, StopDBInstanceRequest

from .base import BaseProvider, CloudResource, ProviderError, mib_to_gb

PAGE_SIZE = 100


class AliyunProvider(BaseProvider):
    provider_name = "alibaba"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._client = AcsClient(self.ak, self.sk, self.region, timeout=30)

    # ------------------------------------------------------------------
    # 底层调用
    # ------------------------------------------------------------------
    def _do(self, request) -> dict:
        request.set_accept_format("JSON")
        try:
            raw = self._client.do_action_with_exception(request)
        except ServerException as exc:
            raise ProviderError(
                f"{exc.get_error_msg()}",
                code=str(exc.get_error_code()),
                request_id=str(exc.get_request_id() or ""),
            ) from exc
        except ClientException as exc:
            raise ProviderError(f"客户端错误: {exc.get_error_msg()}", code=str(exc.get_error_code())) from exc
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(f"请求失败: {exc}") from exc

        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ProviderError(f"响应解析失败: {exc}") from exc

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    def test_connection(self) -> tuple[bool, str]:
        try:
            req = DescribeInstancesRequest.DescribeInstancesRequest()
            req.set_PageSize(1)
            req.set_PageNumber(1)
            data = self._do(req)
            total = data.get("TotalCount", 0)
            return True, f"连接正常（{self.region} 可见 ECS {total} 台）"
        except ProviderError as exc:
            return False, str(exc)

    def list_ecs(self) -> list[CloudResource]:
        out: list[CloudResource] = []
        page = 1
        while True:
            req = DescribeInstancesRequest.DescribeInstancesRequest()
            req.set_PageSize(PAGE_SIZE)
            req.set_PageNumber(page)
            data = self._do(req)

            insts = (data.get("Instances") or {}).get("Instance") or []
            for it in insts:
                vpc = ((it.get("VpcAttributes") or {}).get("VpcId")
                       or it.get("VpcId") or "")
                if not self._match_vpc(vpc):
                    continue
                out.append(CloudResource(
                    resource_id=it.get("InstanceId", ""),
                    resource_name=it.get("InstanceName") or it.get("InstanceId", ""),
                    resource_type="ECS",
                    status=(it.get("Status") or "unknown").lower(),
                    region=self.region,
                    zone=it.get("ZoneId", ""),
                    spec=it.get("InstanceType", ""),
                    cpu=self._to_int(it.get("Cpu")),
                    memory_gb=mib_to_gb(it.get("Memory")),
                    charge_type=self._norm_charge(it.get("InstanceChargeType", "")),
                    private_ip=self._extract_private_ip(it),
                    vpc_id=vpc,
                ))

            total = int(data.get("TotalCount") or 0)
            if len(insts) < PAGE_SIZE or page * PAGE_SIZE >= total:
                break
            page += 1
        return out

    def list_rds(self) -> list[CloudResource]:
        out: list[CloudResource] = []
        page = 1
        while True:
            req = DescribeDBInstancesRequest.DescribeDBInstancesRequest()
            req.set_PageSize(PAGE_SIZE)
            req.set_PageNumber(page)
            data = self._do(req)

            items = (data.get("Items") or {}).get("DBInstance") or []
            for db in items:
                vpc = db.get("VpcId") or ""
                if not self._match_vpc(vpc):
                    continue
                engine = db.get("Engine", "")
                ver = db.get("EngineVersion", "")
                out.append(CloudResource(
                    resource_id=db.get("DBInstanceId", ""),
                    resource_name=db.get("DBInstanceDescription") or db.get("DBInstanceId", ""),
                    resource_type="RDS",
                    status=(db.get("DBInstanceStatus") or "unknown").lower(),
                    region=self.region,
                    zone=db.get("ZoneId", ""),
                    spec=f"{engine} {ver}".strip() or db.get("DBInstanceClass", ""),
                    memory_gb=mib_to_gb(db.get("DBInstanceMemory")),
                    charge_type=self._norm_charge(db.get("PayType", "")),
                    private_ip=db.get("ConnectionString", "") or "",
                    vpc_id=vpc,
                ))

            total = int(data.get("TotalRecordCount") or 0)
            if len(items) < PAGE_SIZE or page * PAGE_SIZE >= total:
                break
            page += 1
        return out

    # ------------------------------------------------------------------
    # 操作
    # ------------------------------------------------------------------
    def start_ecs(self, instance_id: str) -> str:
        req = StartInstancesRequest.StartInstancesRequest()
        req.set_InstanceIds([instance_id])
        data = self._do(req)
        return self._pick_request_id(data, instance_id)

    def stop_ecs(self, instance_id: str, force: bool = False,
                 stopped_mode: str = "StopCharging") -> str:
        req = StopInstancesRequest.StopInstancesRequest()
        req.set_InstanceIds([instance_id])
        req.set_StoppedMode(stopped_mode)
        if force:
            req.set_ForceStop(force)
        data = self._do(req)
        return self._pick_request_id(data, instance_id)

    def start_rds(self, instance_id: str) -> str:
        req = StartDBInstanceRequest.StartDBInstanceRequest()
        req.set_DBInstanceId(instance_id)
        data = self._do(req)
        return data.get("RequestId", "") or ""

    def stop_rds(self, instance_id: str) -> str:
        req = StopDBInstanceRequest.StopDBInstanceRequest()
        req.set_DBInstanceId(instance_id)
        data = self._do(req)
        return data.get("RequestId", "") or ""

    # ------------------------------------------------------------------
    # 工具
    # ------------------------------------------------------------------
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
        if low in ("postpaid", "postpay", "post_paid"):
            return "PostPaid"
        if low in ("prepaid", "prepay", "pre_paid"):
            return "PrePaid"
        return raw or ""

    @staticmethod
    def _extract_private_ip(inst: dict) -> str:
        # 优先取 VpcAttributes.PrivateIpAddress.IpAddress[]
        vpc_attr = inst.get("VpcAttributes") or {}
        ip_list = ((vpc_attr.get("PrivateIpAddress") or {}).get("IpAddress") or [])
        if ip_list:
            return ip_list[0]
        # 其次 NetworkInterfaces
        nics = ((inst.get("NetworkInterfaces") or {}).get("NetworkInterface") or [])
        for nic in nics:
            if nic.get("PrimaryIpAddress"):
                return nic["PrimaryIpAddress"]
        # 公网/私网兜底
        return inst.get("InnerIpAddress", "") or ""

    @staticmethod
    def _pick_request_id(data: dict, instance_id: str) -> str:
        """批量接口返回 InstanceResponses，取当次实例的 RequestId。"""
        try:
            for item in ((data.get("InstanceResponses") or {}).get("InstanceResponse") or []):
                if item.get("InstanceId") == instance_id:
                    return item.get("RequestId", "") or data.get("RequestId", "")
        except (AttributeError, TypeError):
            pass
        return data.get("RequestId", "") or ""
