import asyncio
import io
import json
import os
import tempfile
from collections.abc import AsyncGenerator, Mapping

import qrcode
from bilibili_api import Credential
from curl_cffi.requests import AsyncSession

from nonebot import logger

from ...config import PluginConfig

QR_GENERATE_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
QR_POLL_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/poll"

# 轮询接口返回的状态码
QR_STATUS_SUCCESS = 0
QR_STATUS_SCAN = 86101
QR_STATUS_CONFIRM = 86090
QR_STATUS_EXPIRED = 86038

QR_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.bilibili.com/",
}

CREDENTIAL_KEYS = ("SESSDATA", "bili_jct", "DedeUserID")


def parse_qr_login_cookies(
    payload: dict, set_cookie_headers: list[str] | None = None
) -> dict[str, str]:
    """从扫码成功的轮询响应中提取登录 Cookies

    旧接口把 Cookies 拼在 `url` 查询参数里, 新接口改为通过 `Set-Cookie` 响应头下发,
    两种格式都解析。
    """
    cookies: dict[str, str] = {}

    # 1) url 查询参数: 部分返回以 `&amp;` 拼接, 参数名会残留 `amp;` 前缀
    query = (payload.get("url") or "").partition("?")[2]
    for chunk in query.split("&"):
        name, _, value = chunk.partition("=")
        name = name.strip().removeprefix("amp;")
        if name in CREDENTIAL_KEYS and value:
            cookies.setdefault(name, value.strip())

    # 2) Set-Cookie 响应头
    for header in set_cookie_headers or []:
        name, _, value = header.partition("=")
        name = name.strip()
        if name in CREDENTIAL_KEYS and value:
            cookies.setdefault(name, value.split(";")[0].strip())

    return cookies


class BilibiliLogin:
    """哔哩哔哩登录类"""

    def __init__(self, config: PluginConfig):
        self.credential_file = config.data_dir / "cookies" / "bilibili_credential.json"
        self.config_path = getattr(config, "config_path", None)
        self.raw_cookies = config.parser.bilibili.cookies
        self._credential: Credential | None = None
        self._qr_key: str | None = None
        self._credential_mtime = self._credential_file_mtime()

    def _credential_file_mtime(self):
        try:
            return self.credential_file.stat().st_mtime_ns
        except OSError:
            return None

    def _credential_file_is_newer_than_config(self) -> bool:
        """Dashboard login writes both files; the credential file is authoritative.

        A running bot may still hold the raw cookie string from startup. If the
        dashboard has just saved a QR/paste login, prefer the newer credential
        file even when the process has not initialized its credential yet.
        """
        file_mtime = self._credential_file_mtime()
        if file_mtime is None:
            return False
        config_path = getattr(self, "config_path", None)
        if config_path is None:
            return True
        try:
            config_mtime = config_path.stat().st_mtime_ns
        except OSError:
            return True
        return file_mtime >= config_mtime

    def _reload_if_changed(self) -> None:
        """Reload a credential file written by the dashboard without a restart."""
        if self._credential is None:
            return
        mtime = self._credential_file_mtime()
        if mtime is None or mtime == self._credential_mtime:
            return
        self._credential = None
        self._load_credential()
        self._credential_mtime = mtime

    def _save_credential(self):
        """存储哔哩哔哩登录凭证"""
        if self._credential is None:
            return
        if not self._credential.has_sessdata():
            logger.warning("哔哩哔哩凭证缺少 SESSDATA, 跳过保存")
            return

        cookies = {
            name: value
            for name, value in self._credential.get_cookies().items()
            if value
        }
        self.credential_file.parent.mkdir(parents=True, exist_ok=True)
        temp = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=self.credential_file.parent,
                prefix=".bili-",
                suffix=".tmp",
                delete=False,
            ) as f:
                temp = f.name
                json.dump(cookies, f, ensure_ascii=False)
            os.chmod(temp, 0o600)
            os.replace(temp, self.credential_file)
        finally:
            if temp and os.path.exists(temp):
                os.unlink(temp)
        self._credential_mtime = self._credential_file_mtime()

    def _load_credential(self):
        """从文件加载哔哩哔哩登录凭证"""
        if not self.credential_file.exists():
            return
        try:
            if self.credential_file.stat().st_size > 65536:
                logger.warning(f"哔哩哔哩凭证文件过大, 拒绝加载: {self.credential_file}")
                return
            cookies = json.loads(self.credential_file.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            logger.warning(f"哔哩哔哩凭证文件无法读取: {self.credential_file}: {e}")
            self._credential_mtime = self._credential_file_mtime()
            return
        if not isinstance(cookies, dict):
            logger.warning(f"哔哩哔哩凭证文件格式无效: {self.credential_file}")
            self._credential_mtime = self._credential_file_mtime()
            return
        if "SESSDATA" not in cookies and "sessdata" in cookies:
            cookies["SESSDATA"] = cookies["sessdata"]
        if "DedeUserID" not in cookies and "dedeuserid" in cookies:
            cookies["DedeUserID"] = cookies["dedeuserid"]
        credential = Credential.from_cookies(cookies)
        if credential.has_sessdata():
            self._credential = credential
        else:
            logger.warning(f"哔哩哔哩凭证文件缺少 SESSDATA: {self.credential_file}")
        self._credential_mtime = self._credential_file_mtime()

    def save_cookies(self, cookies: Mapping[str, str], ac_time_value: str = "") -> None:
        """Save cookies supplied by the dashboard (paste import or QR login)."""
        values = {name: str(cookies.get(name) or "").strip() for name in CREDENTIAL_KEYS}
        if not values["SESSDATA"]:
            raise ValueError("缺少 SESSDATA")
        self._credential = Credential(
            sessdata=values["SESSDATA"],
            bili_jct=values["bili_jct"],
            dedeuserid=values["DedeUserID"],
            ac_time_value=ac_time_value,
        )
        self._save_credential()

    async def login_with_qrcode(self) -> bytes:
        """通过二维码登录获取哔哩哔哩登录凭证"""
        async with AsyncSession(impersonate="chrome131", headers=QR_HEADERS) as session:
            resp = await session.get(QR_GENERATE_URL, timeout=15)
            payload = (resp.json() or {}).get("data") or {}

        url = payload.get("url")
        qr_key = payload.get("qrcode_key")
        if not url or not qr_key:
            raise ValueError(f"获取哔哩哔哩登录二维码失败: {payload}")

        self._qr_key = qr_key
        qr = qrcode.QRCode(
            error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=10, border=2
        )
        qr.add_data(url)
        qr.make(fit=True)
        buffer = io.BytesIO()
        qr.make_image(fill_color="black", back_color="white").save(buffer, format="PNG")
        return buffer.getvalue()

    async def poll_qr_state(self) -> dict:
        """Poll the Bilibili QR endpoint once.

        Returns a status dict so the dashboard can poll over HTTP without
        holding a long-lived generator. `check_qr_state()` keeps the original
        bot-side command behavior on top of this.
        """
        if not self._qr_key:
            return {"status": "missing", "message": "请先生成二维码"}
        async with AsyncSession(impersonate="chrome131", headers=QR_HEADERS) as session:
            resp = await session.get(
                QR_POLL_URL, params={"qrcode_key": self._qr_key}, timeout=15
            )
            payload = (resp.json() or {}).get("data") or {}
            status = payload.get("code")
            set_cookie_headers = resp.headers.get_list("set-cookie")

        if status == QR_STATUS_SUCCESS:
            cookies = parse_qr_login_cookies(payload, set_cookie_headers)
            self._qr_key = None
            if not cookies.get("SESSDATA"):
                return {"status": "error", "message": "登录成功, 但未能提取 SESSDATA, 请重新扫码"}
            return {
                "status": "success",
                "message": "登录成功",
                "cookies": cookies,
                "refresh_token": payload.get("refresh_token") or "",
            }
        if status == QR_STATUS_CONFIRM:
            return {"status": "scanned", "message": "二维码已扫描, 请确认登录"}
        if status == QR_STATUS_EXPIRED:
            self._qr_key = None
            return {"status": "expired", "message": "二维码过期, 请重新生成"}
        return {"status": "waiting", "message": "等待扫码"}

    async def check_qr_state(self) -> AsyncGenerator[str, None]:
        """检查二维码登录状态"""
        if not self._qr_key:
            yield "未找到二维码, 请重新生成"
            return

        scan_tip_pending = True
        for _ in range(30):
            result = await self.poll_qr_state()
            status = result.get("status")
            if status == "success":
                self.save_cookies(
                    result.get("cookies") or {},
                    result.get("refresh_token") or "",
                )
                yield "登录成功"
                return
            if status == "scanned":
                if scan_tip_pending:
                    yield result.get("message") or "二维码已扫描, 请确认登录"
                    scan_tip_pending = False
            elif status in {"expired", "error", "missing"}:
                yield result.get("message") or "二维码登录失败, 请重新生成"
                return
            await asyncio.sleep(2)

        yield "二维码登录超时, 请重新生成"

    def _cookies_to_dict(self, cookies_str: str) -> dict[str, str]:
        """将 cookies 字符串转换为字典"""
        res = {}
        for cookie in cookies_str.split(";"):
            name, value = cookie.strip().split("=", 1)
            res[name] = value
        return res

    def subscription_cookies(self) -> dict[str, str]:
        """Snapshot for subscriptions: no validation, refresh, QR or writeback.

        Reuse this parser's current account, falling back to its existing
        configured/saved credential only. The caller handles missing/expired
        login; it must never silently switch to anonymous requests.
        """
        self._reload_if_changed()
        credential = self._credential
        if credential is None or not credential.has_sessdata():
            if self._credential_file_is_newer_than_config():
                self._load_credential()
                credential = self._credential
            if credential is None or not credential.has_sessdata():
                if self.raw_cookies:
                    credential = Credential.from_cookies(self._cookies_to_dict(self.raw_cookies))
                else:
                    if self.credential_file.exists() and self.credential_file.stat().st_size > 65536:
                        raise ValueError('Bilibili credential file too large')
                    self._load_credential()
                    credential = self._credential
        if credential is None or not credential.has_sessdata():
            return {}
        cookies = credential.get_cookies()
        return {name: cookies[name] for name in CREDENTIAL_KEYS if cookies.get(name)}

    async def _init_credential(self):
        """初始化哔哩哔哩登录凭证"""
        if self._credential_file_is_newer_than_config():
            self._load_credential()
            if self._credential is not None and self._credential.has_sessdata():
                return
        if not self.raw_cookies:
            self._load_credential()
            return

        credential = Credential.from_cookies(self._cookies_to_dict(self.raw_cookies))
        try:
            ck_valid = await credential.check_valid()
        except Exception:
            # 检查接口偶发异常(如 -101), 按未通过处理, 回退到凭证文件
            ck_valid = False
        if ck_valid:
            logger.info(f"`parser_bili_ck` 有效, 保存到 {self.credential_file}")
            self._credential = credential
            self._save_credential()
        else:
            logger.info(f"`parser_bili_ck` 已过期, 尝试从 {self.credential_file} 加载")
            self._load_credential()

    @property
    async def credential(self) -> Credential | None:
        """哔哩哔哩登录凭证"""

        self._reload_if_changed()
        if self._credential is None:
            await self._init_credential()
            return self._credential

        if not self._credential.has_sessdata():
            self._load_credential()
            if self._credential is None or not self._credential.has_sessdata():
                logger.warning("哔哩哔哩凭证缺少 SESSDATA, 请重新登录")
                return None

        try:
            cv_valid = await self._credential.check_valid()
        except Exception:
            # 检查接口偶发异常, 按未通过处理
            cv_valid = False
        if not cv_valid:
            # bilibili-api 的 check_valid 对有效凭证也会间歇性返回 False
            # (接口误报), 同一凭证往往仍可正常解析, 告警放行而非直接拦截
            logger.warning("哔哩哔哩凭证 check_valid 未通过(可能为接口误报, 跳过拦截继续使用)")

        try:
            need_refresh = await self._credential.check_refresh()
        except Exception:
            # check_refresh 对失效/被风控凭证会抛 ResponseCodeException(-101),
            # 异常时跳过刷新检查, 不中断解析
            need_refresh = False
        if need_refresh:
            logger.info("哔哩哔哩凭证需要刷新")
            if self._credential.has_ac_time_value() and self._credential.has_bili_jct():
                try:
                    await self._credential.refresh()
                except Exception as e:
                    logger.warning(f"哔哩哔哩凭证刷新失败, 继续使用当前凭证: {e!r}")
                else:
                    logger.info(f"哔哩哔哩凭证刷新成功, 保存到 {self.credential_file}")
                    self._save_credential()
            else:
                logger.warning(
                    "哔哩哔哩凭证刷新需要包含 `SESSDATA`, `ac_time_value` 项"
                )

        return self._credential
