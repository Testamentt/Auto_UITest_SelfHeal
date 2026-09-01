"""管伊佳 ERP 后端 API 客户端（T23）：登录态 + 测试数据造数/清理。

定位：**测试基建而非被测对象**——UI 自动化的被测对象是 ERP 前端（Playwright）；
后端 API 仅用于数据准备（比 UI 造数快且稳）与用例后清理（数据隔离约定）。

标准库零第三方依赖（urllib/hashlib），propose-pr 式独立脚本风格。

勘测结论（2026-08-31，见 docs/sessions/）：
- 登录：POST /user/login，body={loginName, password}，**password 需 MD5 后提交**；
- 响应：{code: 200, data: {msgTip, token}}，token 为字符串；
- 鉴权头：X-Access-Token（/depot/getAllList 实测 200）；
- 列表查询：search 参数为 JSON 字符串（jshERP 惯例）。
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlencode


class ErpApiError(RuntimeError):
    """ERP 后端调用失败（HTTP 错误或业务 code 非 200）。"""


@dataclass
class ErpCredentials:
    """ERP 登录凭证（password 为明文，提交前 MD5——勘测确认前端同款处理）。"""

    username: str
    password: str

    @classmethod
    def from_env(cls, username_env: str, password_env: str) -> ErpCredentials:
        username = os.getenv(username_env, "")
        password = os.getenv(password_env, "")
        if not username or not password:
            raise ErpApiError(f"ERP 凭证缺失：请在 .env 配置 {username_env} / {password_env}")
        return cls(username=username, password=password)


class ErpClient:
    """轻量 jshERP 后端客户端：login() 取 token，最小造数/清理方法集（首轮场景）。"""

    def __init__(self, api_base_url: str, credentials: ErpCredentials, timeout_s: float = 15.0):
        self._base = api_base_url.rstrip("/")
        self._credentials = credentials
        self._timeout = timeout_s
        self._token: str | None = None

    # -- 登录态 --

    def login(self) -> str:
        """登录并缓存 token（验证码已关；password MD5 后提交）。"""
        body = self._request(
            "POST",
            "/user/login",
            payload={
                "loginName": self._credentials.username,
                "password": hashlib.md5(self._credentials.password.encode()).hexdigest(),
            },
        )
        data = body.get("data") or {}
        token = data.get("token")
        if not isinstance(token, str) or not token:
            raise ErpApiError(f"登录未返回 token：msgTip={data.get('msgTip')!r}")
        self._token = token
        return token

    # -- 底层请求 --

    def _request(
        self, method: str, path: str, payload: dict | None = None, params: dict | None = None
    ) -> dict:
        url = self._base + path
        if params:
            url += "?" + urlencode(params)
        headers = {"Content-Type": "application/json"}
        if self._token:
            headers["X-Access-Token"] = self._token
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode() if payload is not None else None,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise ErpApiError(f"{method} {path} HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise ErpApiError(f"{method} {path} 连接失败（ERP 未启动？）：{exc.reason}") from exc
        code = body.get("code")
        if code not in (200, None):
            raise ErpApiError(
                f"{method} {path} 业务失败 code={code}: {str(body.get('data'))[:160]}"
            )
        return body

    @staticmethod
    def _search_param(**filters) -> dict[str, str]:
        """jshERP 惯例：列表接口的 search 参数是 JSON 字符串（分页字段必带）。"""
        return {"search": json.dumps({"currentPage": 1, "pageSize": 20, **filters})}

    # -- 商品造数/清理（T23 首轮场景） --

    def create_material(
        self, name: str, *, standard: str = "标准", model: str = "M-1", unit: str = "个"
    ) -> None:
        """新增商品（UI 测试的前置数据；字段勘测自 /material/add）。"""
        self._request(
            "POST",
            "/material/add",
            payload={"name": name, "standard": standard, "model": model, "unit": unit},
        )

    def find_material_id(self, name: str) -> int | None:
        """按名称查商品 id（add 响应不带 id，经 list 反查）。"""
        body = self._request("GET", "/material/list", params=self._search_param(name=name))
        rows = ((body.get("data") or {}).get("rows")) or []
        for row in rows:
            if row.get("name") == name:
                return int(row["id"])
        return None

    def delete_material(self, material_id: int) -> None:
        """删除商品（勘测确认：单条 delete 会 500，批量接口 deleteBatch 可用）。"""
        self._request("DELETE", "/material/deleteBatch", params={"ids": str(material_id)})

    # -- 供应商造数/清理 --

    def create_supplier(
        self, name: str, *, contacts: str = "测试联系人", phone: str = "13800000000"
    ) -> None:
        self._request(
            "POST",
            "/supplier/add",
            payload={"supplier": name, "contacts": contacts, "telephone": phone, "type": "供应商"},
        )

    def find_supplier_id(self, name: str) -> int | None:
        body = self._request("GET", "/supplier/list", params=self._search_param(name=name))
        rows = ((body.get("data") or {}).get("rows")) or []
        for row in rows:
            if row.get("supplier") == name:
                return int(row["id"])
        return None

    def delete_supplier(self, supplier_id: int) -> None:
        """删除供应商（勘测确认：deleteBatch 可用；若紧随创建立即删除偶发 500，重试即可）。"""
        self._request("DELETE", "/supplier/deleteBatch", params={"ids": str(supplier_id)})
