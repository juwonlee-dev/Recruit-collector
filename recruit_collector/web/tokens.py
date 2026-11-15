"""로그인 토큰: Access(JWT, 15분) + Refresh(JWT, 14일, DB 기록).

두 토큰 모두 HttpOnly 쿠키에 담는다. 화면은 서버가 만들어 보내므로, Access가 만료되면 요청을 받을 때
서버가 Refresh로 새 토큰을 발급해 응답 쿠키에 넣는다 (화면 JS는 토큰을 다루지 않음).

- Access: 서명만 확인하고 DB 없이 믿는다. 단 사용자를 읽을 때 그 로그인(fam)이 끊겼는지도 함께 본다(app.get_access).
- Refresh: 쓸 때마다 새 것으로 바꾸고(rotation) 만료를 14일 늘린다. 로그인한 때부터 최대 60일.
  이미 바뀐 옛 토큰이 REUSE_GRACE 안에 다시 오면 동시에 보낸 요청으로 보고 지금 토큰을 다시 주고,
  그 뒤에 오면 토큰이 새어 나간 것으로 보고 그 로그인을 끊는다.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import jwt
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from recruit_collector.models import LoginSession, utcnow

ACCESS_COOKIE = "rc_access"
REFRESH_COOKIE = "rc_refresh"
ACCESS_TTL = timedelta(minutes=15)
REFRESH_TTL = timedelta(days=14)
MAX_LOGIN_AGE = timedelta(days=60)
REUSE_GRACE = timedelta(seconds=60)
ALGORITHM = "HS256"


@dataclass
class Login:
    """이 요청의 로그인 상태. 응답을 보낼 때 미들웨어가 set_*/clear에 따라 쿠키를 고친다."""

    uid: int | None = None
    fam: str | None = None
    set_access: str | None = None
    set_refresh: tuple[str, datetime] | None = None  # (토큰, 만료 시각)
    clear: bool = False

    def signed_out(self) -> None:
        self.uid = self.fam = self.set_access = self.set_refresh = None
        self.clear = True


def _ts(dt: datetime) -> int:
    return int(dt.replace(tzinfo=timezone.utc).timestamp())


def _access(secret: str, uid: int, fam: str) -> str:
    now = utcnow()
    return jwt.encode({"typ": "access", "sub": str(uid), "fam": fam, "iat": _ts(now), "exp": _ts(now + ACCESS_TTL)},
                      secret, algorithm=ALGORITHM)


def _refresh(secret: str, row: LoginSession) -> str:
    # 같은 값이면 같은 토큰이 나온다 (동시 요청에 지금 토큰을 다시 줄 때 씀)
    return jwt.encode({"typ": "refresh", "sub": str(row.user_id), "fam": row.id, "jti": row.current_jti,
                       "exp": _ts(row.expires_at)}, secret, algorithm=ALGORITHM)


def decode(secret: str, token: str | None, typ: str) -> dict | None:
    if not token:
        return None
    try:
        claims = jwt.decode(token, secret, algorithms=[ALGORITHM], options={"require": ["exp", "sub", "fam"]})
    except jwt.InvalidTokenError:
        return None
    if claims.get("typ") != typ or not str(claims["sub"]).isdigit():
        return None
    return claims


def sign_in(s: Session, secret: str, uid: int, login: Login,
            user_agent: str | None = None, ip: str | None = None) -> None:
    """구글 로그인을 마친 뒤: 새 로그인(fam)을 기기·IP와 함께 기록하고 두 토큰을 발급한다."""
    now = utcnow()
    s.execute(delete(LoginSession).where(LoginSession.expires_at < now - timedelta(days=1)))  # 지난 기록 정리
    row = LoginSession(id=secrets.token_urlsafe(32), user_id=uid, started_at=now,
                       expires_at=min(now + REFRESH_TTL, now + MAX_LOGIN_AGE), current_jti=secrets.token_urlsafe(32),
                       user_agent=(user_agent or "")[:400] or None, ip=(ip or "")[:64] or None)
    s.add(row)
    s.commit()
    login.uid, login.fam, login.clear = uid, row.id, False
    login.set_access = _access(secret, uid, row.id)
    login.set_refresh = (_refresh(secret, row), row.expires_at)


def refresh(s: Session, secret: str, token: str | None) -> Login:
    """Access가 없거나 만료됐을 때 Refresh로 새 토큰을 발급한다. 안 되면 쿠키를 지우라고(clear) 돌려준다."""
    login = Login()
    claims = decode(secret, token, "refresh")
    if claims is None or not claims.get("jti"):
        login.clear = token is not None
        return login
    now = utcnow()
    row = s.get(LoginSession, claims["fam"])
    if row is None or row.revoked_at is not None or row.expires_at <= now or str(row.user_id) != claims["sub"]:
        login.clear = True
        return login
    jti = claims["jti"]
    if jti == row.current_jti:
        new_jti = secrets.token_urlsafe(32)
        expires = min(now + REFRESH_TTL, row.started_at + MAX_LOGIN_AGE)
        # 동시에 온 요청이 같은 토큰으로 먼저 바꿨으면 0건 → 아래 '옛 토큰' 경우로 처리
        done = s.execute(update(LoginSession)
                         .where(LoginSession.id == row.id, LoginSession.current_jti == jti)
                         .values(current_jti=new_jti, previous_jti=jti, rotated_at=now, expires_at=expires)).rowcount
        s.commit()
        s.refresh(row)
        if done:
            login.uid, login.fam = row.user_id, row.id
            login.set_access = _access(secret, row.user_id, row.id)
            login.set_refresh = (_refresh(secret, row), row.expires_at)
            return login
    if jti == row.previous_jti and row.rotated_at and now - row.rotated_at <= REUSE_GRACE:
        login.uid, login.fam = row.user_id, row.id
        login.set_access = _access(secret, row.user_id, row.id)
        login.set_refresh = (_refresh(secret, row), row.expires_at)
        return login
    # 이미 바뀐 옛 토큰이 다시 왔다 → 토큰이 새어 나간 것으로 보고 이 로그인을 끊는다
    row.revoked_at = now
    s.commit()
    login.clear = True
    return login


def is_active(s: Session, fam: str | None, uid: int) -> bool:
    row = s.get(LoginSession, fam) if fam else None
    return row is not None and row.user_id == uid and row.revoked_at is None


def revoke(s: Session, fam: str | None) -> bool:
    """이 로그인(기기 하나)을 끊는다. 끊었으면 True."""
    if not fam:
        return False
    n = s.execute(update(LoginSession).where(LoginSession.id == fam, LoginSession.revoked_at.is_(None))
                  .values(revoked_at=utcnow())).rowcount
    s.commit()
    return bool(n)


def revoke_user(s: Session, uid: int, keep: str | None = None) -> int:
    """이 사용자의 모든 로그인을 끊는다 (모든 기기에서 로그아웃). keep은 남길 로그인(지금 쓰는 기기). 끊은 개수."""
    stmt = (update(LoginSession)
            .where(LoginSession.user_id == uid, LoginSession.revoked_at.is_(None), LoginSession.expires_at > utcnow()))
    if keep:
        stmt = stmt.where(LoginSession.id != keep)
    n = s.execute(stmt.values(revoked_at=utcnow())).rowcount
    s.commit()
    return n


def active_sessions(s: Session) -> dict[int, list[LoginSession]]:
    """사용자별 로그인 중인 기기 (최근 로그인 먼저)."""
    rows = s.scalars(select(LoginSession)
                     .where(LoginSession.revoked_at.is_(None), LoginSession.expires_at > utcnow())
                     .order_by(LoginSession.started_at.desc()))
    out: dict[int, list[LoginSession]] = {}
    for row in rows:
        out.setdefault(row.user_id, []).append(row)
    return out


_BROWSERS = [("KAKAOTALK", "카카오톡"), ("NAVER", "네이버 앱"), ("SamsungBrowser", "삼성 인터넷"), ("Whale/", "웨일"),
             ("Edg", "Edge"), ("OPR/", "Opera"), ("Firefox/", "Firefox"), ("FxiOS", "Firefox"),
             ("CriOS", "Chrome"), ("Chrome/", "Chrome"), ("Safari/", "Safari")]


# 안드로이드 UA의 기종: "Android 15; SM-S928N Build/..." → SM-S928N.
# 요즘 크롬은 기종을 "K"로 가려 보내므로 그때는 기종을 모른다 (삼성 인터넷·앱 내 브라우저 등은 보냄)
_ANDROID_MODEL = re.compile(r"Android[ /]([\d.]+)?;(?: [a-z]{2}[-_][A-Za-z]{2};)? ([^;)]+?)(?: Build/[^;)]*)?[;)]")
_HIDDEN_MODELS = {"K", "wv", "Mobile", "Linux", "U"}
KIND_LABELS = {"pc": "PC", "mobile": "휴대폰", "tablet": "태블릿", "unknown": "알 수 없는 기기"}


@dataclass
class Device:
    kind: str  # pc / mobile / tablet / unknown
    name: str  # 기종 (예: 갤럭시 SM-S928N, iPhone, Windows PC). 모르면 '알 수 없는 기기'
    detail: str  # 운영체제 · 브라우저 (예: Android 15 · 삼성 인터넷)

    @property
    def kind_label(self) -> str:
        return KIND_LABELS[self.kind]

    @property
    def label(self) -> str:
        return " · ".join(x for x in (self.name, self.detail) if x)


def device_info(user_agent: str | None) -> Device:
    """User-Agent로 기기 종류(PC·휴대폰·태블릿)와 알 수 있는 만큼의 기종·운영체제·브라우저를 추정한다.

    iPadOS 13 이후의 사파리는 Mac과 같은 UA를 보내 서버에서는 Mac(PC)로 보인다.
    """
    ua = user_agent or ""
    browser = next((label for key, label in _BROWSERS if key in ua), None)
    kind, name, os_name = "unknown", None, None
    if "iPad" in ua:
        kind, name = "tablet", "iPad"
    elif "iPhone" in ua or "iPod" in ua:
        kind, name = "mobile", "iPhone" if "iPhone" in ua else "iPod"
    elif "Android" in ua:
        kind = "mobile" if "Mobile" in ua else "tablet"
        m = _ANDROID_MODEL.search(ua)
        os_name = f"Android {m.group(1)}" if m and m.group(1) else "Android"
        model = m.group(2).strip() if m else ""
        if model and model not in _HIDDEN_MODELS:
            name = f"갤럭시 {model}" if model.startswith("SM-") else model
        else:
            name = f"Android {KIND_LABELS[kind]}"
    elif "Windows" in ua:
        kind, name = "pc", "Windows PC"
    elif "Macintosh" in ua:
        kind, name = "pc", "Mac"
    elif "CrOS" in ua:
        kind, name = "pc", "크롬북"
    elif "Linux" in ua or "X11" in ua:
        kind, name = "pc", "Linux PC"
    if kind in ("mobile", "tablet") and name in ("iPhone", "iPod", "iPad"):
        m = re.search(r"OS (\d+(?:_\d+)*) like Mac OS X", ua)
        os_name = f"iOS {m.group(1).replace('_', '.')}" if m else "iOS"
        if name == "iPad":
            os_name = os_name.replace("iOS", "iPadOS")
    detail = " · ".join(x for x in (os_name, browser) if x)
    return Device(kind, name or KIND_LABELS["unknown"], detail)


def device_label(user_agent: str | None) -> str:
    """로그아웃 안내 문구용 한 줄 (예: 갤럭시 SM-S928N · Android 15 · 삼성 인터넷)."""
    return device_info(user_agent).label
