"""通報。当面はメールのみ。

--------------------------------------------------------------------------
設計方針
--------------------------------------------------------------------------
本文に画像を添付せず、確認ページのURLを1本だけ送る。

  - 添付が重いとモバイル回線で開かない。迷惑メール判定も受けやすくなる
  - 受領確認(ack)を確認ページ側で取れるので、手段ごとに ack の仕組みを
    作り分けなくてよい
  - あとで LINE や SMS を足すとき、本文がそのまま流用できる。SMS の70文字にも収まる

Notifier を差し替えれば手段を追加できる。当面 EmailNotifier のみを実装する。

--------------------------------------------------------------------------
取消通知
--------------------------------------------------------------------------
事後判定や人手の再確認で誤報と分かった場合、同じ経路で取消を送れるようにしてある。
猟友会・警察との関係を保つうえで、通報そのものと同じくらい重要。
"""

from __future__ import annotations

import logging
import smtplib
import ssl
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid

log = logging.getLogger(__name__)


# ---------------------------------------------------------------- データ

@dataclass
class Alert:
    event_id: str
    device_id: str
    site_name: str                     # 「○○地区 north 農道」など人が分かる名前
    detected_at_local: str             # "2026-08-08 14:32"
    confidence: float                  # 0-1
    azimuth_deg: float | None = None
    distance_m: float | None = None
    lat: float | None = None
    lon: float | None = None
    species: str = "ツキノワグマ"
    reviewer: str | None = None        # 人手確認者。自動判定なら None
    confirm_url: str = ""              # 確認ページ。ack はここで取る


@dataclass
class Retraction:
    event_id: str
    site_name: str
    detected_at_local: str
    reason: str                        # 「事後判定でイノシシと判明」など
    confirm_url: str = ""


@dataclass
class DeliveryResult:
    ok: bool
    channel: str
    recipients: list[str] = field(default_factory=list)
    message_id: str | None = None
    error: str | None = None
    attempts: int = 0


# ---------------------------------------------------------------- 抽象

class Notifier(ABC):
    channel = "base"

    @abstractmethod
    def send_alert(self, alert: Alert) -> DeliveryResult: ...

    @abstractmethod
    def send_retraction(self, r: Retraction) -> DeliveryResult: ...


# ---------------------------------------------------------------- 本文

def _fmt_position(a: Alert) -> str:
    parts = []
    if a.azimuth_deg is not None:
        parts.append(f"方位 {a.azimuth_deg:.0f}度")
    if a.distance_m is not None:
        parts.append(f"距離 約{a.distance_m:.0f}m")
    return " / ".join(parts) if parts else "位置情報なし"


def build_alert_body(a: Alert) -> tuple[str, str]:
    """(件名, 本文) を返す。短く、URLを目立たせる。"""
    subject = f"【クマ検知】{a.site_name} {a.detected_at_local}"
    judge = f"人手確認済み（{a.reviewer}）" if a.reviewer else "自動判定"
    lines = [
        f"{a.site_name} で{a.species}を検知しました。",
        "",
        f"  日時    {a.detected_at_local}",
        f"  地点    {a.site_name}（端末 {a.device_id}）",
        f"  位置    {_fmt_position(a)}",
        f"  確度    {a.confidence * 100:.0f}%（{judge}）",
        "",
        "画像・映像・地図はこちらで確認できます。",
        f"  {a.confirm_url}",
        "",
        "同じページで受領の登録ができます。受領がない場合、",
        "別の手段で再度ご連絡します。",
        "",
        f"（管理番号 {a.event_id}）",
    ]
    if a.lat is not None and a.lon is not None:
        lines.insert(5, f"  座標    {a.lat:.6f}, {a.lon:.6f}")
    return subject, "\n".join(lines)


def build_retraction_body(r: Retraction) -> tuple[str, str]:
    subject = f"【取消】クマ検知の通報 {r.site_name} {r.detected_at_local}"
    body = "\n".join([
        f"先ほどお送りした {r.site_name} {r.detected_at_local} の通報を取り消します。",
        "",
        f"  理由    {r.reason}",
        "",
        "お手数をおかけしました。",
        f"  {r.confirm_url}" if r.confirm_url else "",
        "",
        f"（管理番号 {r.event_id}）",
    ])
    return subject, body


# ---------------------------------------------------------------- メール実装

class EmailNotifier(Notifier):
    channel = "email"

    def __init__(self, host: str, port: int, username: str | None, password: str | None,
                 sender: str, sender_name: str, recipients: list[str],
                 use_starttls: bool = True, use_ssl: bool = False,
                 timeout: int = 20, max_attempts: int = 3, retry_wait_s: float = 5.0,
                 dry_run: bool = False):
        self.host, self.port = host, port
        self.username, self.password = username, password
        self.sender, self.sender_name = sender, sender_name
        self.recipients = list(recipients)
        self.use_starttls, self.use_ssl = use_starttls, use_ssl
        self.timeout = timeout
        self.max_attempts, self.retry_wait_s = max_attempts, retry_wait_s
        self.dry_run = dry_run
        self.sent: list[EmailMessage] = []      # dry_run 時の確認用

    # -------------------------------------------------- 送信

    def _build(self, subject: str, body: str, priority: bool) -> EmailMessage:
        m = EmailMessage()
        m["From"] = formataddr((self.sender_name, self.sender))
        m["To"] = ", ".join(self.recipients)
        m["Subject"] = subject
        m["Date"] = formatdate(localtime=True)
        m["Message-ID"] = make_msgid()
        if priority:
            m["X-Priority"] = "1"
            m["Importance"] = "High"
        m.set_content(body)
        return m

    def _send(self, msg: EmailMessage) -> DeliveryResult:
        if not self.recipients:
            return DeliveryResult(False, self.channel, [], None, "宛先が設定されていません", 0)
        if self.dry_run:
            self.sent.append(msg)
            log.info("dry_run: %s", msg["Subject"])
            return DeliveryResult(True, self.channel, self.recipients,
                                  msg["Message-ID"], None, 1)

        last = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                if self.use_ssl:
                    ctx = ssl.create_default_context()
                    srv = smtplib.SMTP_SSL(self.host, self.port, timeout=self.timeout,
                                           context=ctx)
                else:
                    srv = smtplib.SMTP(self.host, self.port, timeout=self.timeout)
                with srv:
                    srv.ehlo()
                    if self.use_starttls and not self.use_ssl:
                        srv.starttls(context=ssl.create_default_context())
                        srv.ehlo()
                    if self.username:
                        srv.login(self.username, self.password or "")
                    srv.send_message(msg)
                return DeliveryResult(True, self.channel, self.recipients,
                                      msg["Message-ID"], None, attempt)
            except Exception as e:
                last = f"{type(e).__name__}: {e}"
                log.warning("メール送信失敗 (%d/%d): %s", attempt, self.max_attempts, last)
                if attempt < self.max_attempts:
                    time.sleep(self.retry_wait_s * attempt)
        return DeliveryResult(False, self.channel, self.recipients, None, last,
                              self.max_attempts)

    # -------------------------------------------------- 公開

    def send_alert(self, alert: Alert) -> DeliveryResult:
        subject, body = build_alert_body(alert)
        return self._send(self._build(subject, body, priority=True))

    def send_retraction(self, r: Retraction) -> DeliveryResult:
        subject, body = build_retraction_body(r)
        return self._send(self._build(subject, body, priority=False))


# ---------------------------------------------------------------- 束ねる

class NotifierChain:
    """複数の手段を順に試す。当面はメール1つだが、後から追加できるようにしておく。

    すべて失敗した場合は failure_hook を呼ぶ。ここで管理者へのアラートや
    ローカルへの記録を行う。通報が届かなかったことに気づけないのが最悪なので、
    失敗を握り潰さないこと。
    """

    def __init__(self, notifiers: list[Notifier], failure_hook=None):
        self.notifiers = notifiers
        self.failure_hook = failure_hook

    def send_alert(self, alert: Alert) -> list[DeliveryResult]:
        results = []
        for n in self.notifiers:
            r = n.send_alert(alert)
            results.append(r)
            if r.ok:
                return results
        if self.failure_hook:
            self.failure_hook(alert, results)
        return results

    def send_retraction(self, r: Retraction) -> list[DeliveryResult]:
        out = []
        for n in self.notifiers:
            res = n.send_retraction(r)
            out.append(res)
            if res.ok:
                return out
        if self.failure_hook:
            self.failure_hook(r, out)
        return out


# ---------------------------------------------------------------- 設定例

EXAMPLE_CONFIG = {
    "email": {
        "host": "smtp.example.jp",
        "port": 587,
        "username": "bear-watch@example.jp",
        "password": "${SMTP_PASSWORD}",
        "sender": "bear-watch@example.jp",
        "sender_name": "クマ監視システム",
        "recipients": ["ryoyukai@example.jp", "police@example.jp"],
        "use_starttls": True
    },
    "confirm_url_base": "https://bear.example.jp/e/"
}
