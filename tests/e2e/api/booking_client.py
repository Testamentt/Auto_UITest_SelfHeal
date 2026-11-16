# -*- coding: utf-8 -*-
# 本项目统一使用 UTF-8（无 BOM）编码。
"""票多星（booking）后端 API 客户端：管理端登录态 + 客户账号准备。

定位：**测试基建而非被测对象**——UI 自动化的被测对象是票多星前端（Playwright）；
后端 API 仅用于数据准备（注册/复用商城客户账号，比 UI 注册快且稳）。

标准库零第三方依赖（urllib / json / hashlib），自拼请求、错误统一包成
`BookingApiError`。

勘测结论（后端 `booking-api`）：
- 响应统一 `{code, message, data}`，成功 `code=0`；业务失败 HTTP 仍为 200，靠 code 判别；
- 管理端登录 `POST /admin/login`，body `{username, password}`，`data` 含
  `token / userId / username / nickname / expiresIn`；
- 鉴权头 `Authorization: Bearer <token>`（除注册/登录/公开票务接口外均需）；
- 按用户名查用户 `GET /user/username/{username}`，用户不存在时业务 code 非 0；
- 客户注册 `POST /user/register`，**手机号必填**且需合法 11 位（`1[3-9]` 开头）。
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import quote, urlencode


class BookingApiError(RuntimeError):
    """票多星后端调用失败（HTTP 错误、业务 code 非 0 或响应契约不符）。

    `code` 仅在业务失败（响应体 code != 0）时带值，供调用方区分"业务不存在"与
    "后端不可用 / 鉴权失败"。
    """

    def __init__(self, message: str, *, code: int | None = None):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class BookingAdminCredentials:
    """票多星管理员凭证（管理端 UI 登录与 API 鉴权共用同一账号）。"""

    username: str
    password: str

    @classmethod
    def from_env(cls, username_env: str, password_env: str) -> BookingAdminCredentials:
        """从环境变量读取凭证；任一缺失均视为配置问题（提示写进 .env）。"""
        username = os.getenv(username_env, "")
        password = os.getenv(password_env, "")
        if not username or not password:
            raise BookingApiError(
                f"票多星管理员凭证缺失：请在 .env 配置 {username_env} / {password_env}"
            )
        return cls(username=username, password=password)


def _derive_phone(username: str) -> str:
    """由用户名派生唯一合法 11 位手机号（`1[3-9]` 开头），供注册接口的必填校验。

    同一用户名恒得同一号码：重复注册会因手机号已存在而失败，便于 `ensure_customer`
    先查后建时保持幂等语义。
    """
    digest = hashlib.md5(username.encode("utf-8")).hexdigest()
    return "13" + str(int(digest, 16) % 10**9).zfill(9)


class BookingApiClient:
    """轻量票多星后端客户端：管理端登录 + 客户账号查询/注册。"""

    def __init__(
        self, api_base_url: str, credentials: BookingAdminCredentials, timeout_s: float = 15.0
    ):
        self._base = api_base_url.rstrip("/")
        self._credentials = credentials
        self._timeout = timeout_s
        self._token: str | None = None

    # -- 登录态 --

    def login_admin(self) -> str:
        """管理端登录并缓存 token（后续请求自动带 Bearer 头）。"""
        body = self._request(
            "POST",
            "/admin/login",
            payload={
                "username": self._credentials.username,
                "password": self._credentials.password,
            },
        )
        data = body.get("data") or {}
        token = data.get("token")
        if not isinstance(token, str) or not token:
            raise BookingApiError(f"管理端登录未返回 token：{str(data)[:160]}")
        self._token = token
        return token

    # -- 客户账号 --

    def get_user_by_username(self, username: str) -> dict | None:
        """按用户名查用户；不存在（404 / 业务错误）返回 None，不抛。"""
        path = f"/user/username/{quote(username, safe='')}"
        try:
            body = self._request("GET", path)
        except _BookingNotFound:
            return None
        except BookingApiError as exc:
            if exc.code is None:  # 非业务错误（后端不可用 / 鉴权失败）照常上抛
                raise
            return None
        data = body.get("data")
        return data if isinstance(data, dict) else None

    def register_customer(
        self,
        username: str,
        password: str,
        phone: str | None = None,
        nickname: str | None = None,
    ) -> int:
        """注册客户并返回 userId；phone 缺省时由用户名派生唯一合法号码。"""
        payload = {
            "username": username,
            "password": password,
            "phone": phone or _derive_phone(username),
            "nickname": nickname or username,
        }
        body = self._request("POST", "/user/register", payload=payload)
        user_id = body.get("data")
        if not isinstance(user_id, int) or user_id <= 0:
            raise BookingApiError(f"客户注册未返回 userId：{str(user_id)!r}")
        return user_id

    def ensure_customer(self, username: str, password: str) -> int:
        """确保客户账号存在：已存在返回其 userId，否则注册后返回新 userId。"""
        existing = self.get_user_by_username(username)
        if existing is not None:
            user_id = existing.get("userId") or existing.get("id")
            if not isinstance(user_id, int) or user_id <= 0:
                raise BookingApiError(f"客户信息缺少 userId：{str(existing)[:160]}")
            return user_id
        return self.register_customer(username, password)

    # -- 生命周期 --

    def close(self) -> None:
        """释放资源（urllib 无长连接，保持与既有资源释放约定对齐，no-op）。"""

    # -- 底层请求 --

    def _request(
        self, method: str, path: str, payload: dict | None = None, params: dict | None = None
    ) -> dict:
        url = self._base + path
        if params:
            url += "?" + urlencode(params)
        headers = {"Content-Type": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8") if payload is not None else None,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise _BookingNotFound(f"{method} {path} 资源不存在（404）") from exc
            raise BookingApiError(f"{method} {path} HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise BookingApiError(
                f"{method} {path} 连接失败：票多星后端不可用（8088 未启动？）：{exc.reason}"
            ) from exc

        try:
            body = json.loads(raw)
        except ValueError as exc:
            raise BookingApiError(f"{method} {path} 响应非 JSON：{raw[:160]!r}") from exc
        if not isinstance(body, dict):
            raise BookingApiError(f"{method} {path} 响应契约不符：{raw[:160]!r}")
        code = body.get("code")
        if code != 0:
            raise BookingApiError(
                f"{method} {path} 业务失败 code={code} message={str(body.get('message'))[:160]}",
                code=code if isinstance(code, int) else None,
            )
        return body


class _BookingNotFound(BookingApiError):
    """内部标记：资源不存在（供按用户名查询降级为 None，不向调用方抛）。"""
