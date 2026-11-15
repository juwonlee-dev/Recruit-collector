"""구글 로그인과 관리자 권한.

- 로그인 사용 여부는 설정 화면에서 켜고 끈다 (DB app_settings). 꺼져 있으면 모든 기능을 제한 없이 쓴다.
- 켜져 있으면 '지금 수집'과 설정 메뉴는 관리자만, 연봉·인원 메뉴는 로그인한 사용자만 쓸 수 있다.
- 회원가입은 따로 없다. 구글로 처음 로그인하면 자동으로 가입된다 (등급: 일반).
- 관리자: config.yaml auth.admin_emails의 이메일(항상 관리자) + 설정 화면에서 관리자로 지정한 사용자.

구글 OAuth 2.0 인가 코드 방식. 토큰과 사용자 정보는 서버가 구글에 직접(https) 요청해 받는다.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from recruit_collector.config import AuthSettings
from recruit_collector.models import AppSetting, User, utcnow

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"

ADMIN = "admin"
USER = "user"
ROLE_LABELS = {ADMIN: "관리자", USER: "일반"}

LOGIN_ENABLED_KEY = "login_enabled"
SESSION_SECRET_KEY = "session_secret"


class LoginError(Exception):
    """구글 로그인 실패. 메시지는 화면에 그대로 보여준다."""


# ------------------------------------------------------------ 화면 설정 값 (DB)

def get_setting(s: Session, key: str) -> str | None:
    row = s.get(AppSetting, key)
    return row.value if row else None


def set_setting(s: Session, key: str, value: str) -> None:
    row = s.get(AppSetting, key)
    if row:
        row.value = value
    else:
        s.add(AppSetting(key=key, value=value))


def login_enabled(s: Session) -> bool:
    return get_setting(s, LOGIN_ENABLED_KEY) == "1"


def session_secret(s: Session) -> str:
    """로그인 토큰(JWT)·구글 로그인 확인값 쿠키 서명 키. 처음 한 번 만들어 DB에 두고 계속 쓴다 (서버를 다시 띄워도 로그인 유지)."""
    value = get_setting(s, SESSION_SECRET_KEY)
    if not value:
        value = secrets.token_urlsafe(48)
        set_setting(s, SESSION_SECRET_KEY, value)
        s.commit()
    return value


# ------------------------------------------------------------ 권한

def is_admin(user: User | None, settings: AuthSettings) -> bool:
    return bool(user) and (user.role == ADMIN or user.email.lower() in settings.admin_emails)


@dataclass
class Access:
    login_enabled: bool
    google_ready: bool  # config.yaml에 구글 클라이언트 ID·시크릿이 있음
    user: User | None
    is_admin: bool

    @property
    def can_manage(self) -> bool:
        """지금 수집·설정 메뉴를 쓸 수 있는지. 로그인을 끄면 모두 쓸 수 있다."""
        return not self.login_enabled or self.is_admin

    @property
    def can_view_salary(self) -> bool:
        """연봉·인원 메뉴. 로그인 사용 중이면 로그인한 사용자만 (등급 무관)."""
        return not self.login_enabled or self.user is not None

    @property
    def can_favorite(self) -> bool:
        """공고·회사 즐겨찾기. 계정별로 저장하므로 로그인 사용 중이고 로그인한 경우에만."""
        return self.login_enabled and self.user is not None


# ------------------------------------------------------------ 구글 OAuth

def new_state() -> str:
    return secrets.token_urlsafe(24)


def authorize_url(settings: AuthSettings, redirect_uri: str, state: str) -> str:
    return AUTHORIZE_URL + "?" + urlencode({
        "client_id": settings.google_client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "prompt": "select_account",
    })


def fetch_google_user(settings: AuthSettings, code: str, redirect_uri: str) -> dict:
    """인가 코드를 토큰으로 바꾸고 사용자 정보(sub, email, email_verified, name, picture)를 받는다."""
    try:
        with httpx.Client(timeout=15) as client:
            token = client.post(TOKEN_URL, data={
                "code": code,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            })
            if token.status_code != 200:
                raise LoginError(f"구글 토큰 발급에 실패했습니다. (HTTP {token.status_code})")
            info = client.get(USERINFO_URL, headers={"Authorization": f"Bearer {token.json()['access_token']}"})
            if info.status_code != 200:
                raise LoginError(f"구글 사용자 정보를 받지 못했습니다. (HTTP {info.status_code})")
            return info.json()
    except httpx.HTTPError as e:
        raise LoginError(f"구글에 연결하지 못했습니다. ({type(e).__name__})") from e


def sign_in(s: Session, info: dict, settings: AuthSettings) -> User:
    """구글 사용자 정보로 로그인한다. 처음이면 가입시킨다. config의 관리자 이메일은 관리자로 둔다."""
    sub, email = str(info.get("sub") or ""), str(info.get("email") or "").strip().lower()
    if not sub or not email:
        raise LoginError("구글 계정 정보에 이메일이 없습니다.")
    if info.get("email_verified") is not True:
        raise LoginError("이메일이 인증되지 않은 구글 계정입니다.")
    now = utcnow()
    user = s.scalar(select(User).where(User.google_sub == sub))
    if user is None:
        user = User(google_sub=sub, email=email, role=USER, created_at=now, last_login_at=now)
        s.add(user)
    user.email = email
    user.name = info.get("name") or None
    user.picture = info.get("picture") or None
    user.last_login_at = now
    if email in settings.admin_emails:
        user.role = ADMIN
    s.commit()
    return user


def safe_next(url: str | None) -> str:
    """로그인 뒤 돌아갈 주소. 이 사이트 안의 경로만 허용한다."""
    if url and url.startswith("/") and not url.startswith("//") and "\\" not in url:
        return url
    return "/"
