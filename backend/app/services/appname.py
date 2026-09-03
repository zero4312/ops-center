"""应用归属解析：从实例名称自动推导「应用」与「环境」。

命名规范（对齐 stg-patrol 巡检清单的真实数据）：
    <前缀>-<应用>-<环境>-<角色>-<序号/后缀>

示例：
    APC-ACE-STG-AdminJob-L      -> 应用 APC-ACE,      环境 STG, 角色 AdminJob
    APHK-HKIPOS-STG-POS-B       -> 应用 APHK-HKIPOS,  环境 STG, 角色 POS
    APC-Canvas-Creation-DEV-WAS-F -> 应用 APC-Canvas-Creation, 环境 DEV, 角色 WAS
    APC-IBA-STG-K8S-01          -> 应用 APC-IBA,      环境 STG, 角色 K8S

解析策略：
  1. 必须以已知前缀开头（APC / APHK ...），否则归入「未分类」；
  2. 应用名取「前缀之后、环境关键字之前」的全部内容（贪婪匹配），
     这样 Canvas-Creation 这类带子模块的命名能保持独立分组；
  3. 环境关键字大小写不敏感（Stg / STG / Dev 均可识别）。

不符合规范的名称统一归入 UNCLASSIFIED，运维可在「应用管理」中手工调整归属，
手工绑定优先级永远高于自动解析。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# 已知的公司/项目前缀（可扩展）
NAME_PREFIXES = ["APC", "APHK", "APSH", "AP"]

# 环境关键字（按长度降序，避免 SIT 被 STG 之类误伤）
ENV_KEYWORDS = ["PROD", "PRD", "STG", "UAT", "SIT", "DEV", "TEST", "QA"]

UNCLASSIFIED = "未分类"
UNCLASSIFIED_CODE = "__unclassified__"

# 贪婪匹配：应用部分尽可能长，直到遇到最后一个环境关键字
_PATTERN = re.compile(
    r"^(?P<prefix>" + "|".join(NAME_PREFIXES) + r")"
    r"[-_](?P<app>.+)"
    r"[-_](?P<env>" + "|".join(ENV_KEYWORDS) + r")"
    r"(?:[-_](?P<rest>.*))?$",
    re.IGNORECASE,
)


@dataclass
class ParsedName:
    app_code: str        # 应用匹配码，如 APC-ACE
    app_name: str        # 应用展示名，同 code
    env: str             # STG / DEV ...
    role: str            # 角色，如 AdminJob / WEB / DB
    matched: bool        # 是否命中规范


def parse_resource_name(name: str) -> ParsedName:
    """解析实例名 -> 应用 / 环境 / 角色。"""
    raw = (name or "").strip()
    if not raw:
        return ParsedName(UNCLASSIFIED_CODE, UNCLASSIFIED, "", "", False)

    m = _PATTERN.match(raw)
    if not m:
        return ParsedName(UNCLASSIFIED_CODE, UNCLASSIFIED, "", "", False)

    prefix = m.group("prefix").upper()
    app_part = m.group("app").strip()
    env = (m.group("env") or "").upper()
    rest = (m.group("rest") or "").strip()

    if not app_part:
        return ParsedName(UNCLASSIFIED_CODE, UNCLASSIFIED, "", "", False)

    # 角色：环境之后的第一段，用于展示（如 AdminJob / WEB01 / DB）
    role = rest.split("-")[0].strip() if rest else ""

    app_code = f"{prefix}-{app_part}"
    return ParsedName(app_code=app_code, app_name=app_code, env=env, role=role, matched=True)


def extract_env(name: str) -> str:
    """仅提取环境标识，解析失败返回空串。"""
    return parse_resource_name(name).env
