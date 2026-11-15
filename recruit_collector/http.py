"""요청 간 지연, 재시도, robots.txt 준수를 담당하는 HTTP 클라이언트."""

from __future__ import annotations

import logging
import random
import threading
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

log = logging.getLogger(__name__)

RETRY_STATUS = {429, 500, 502, 503, 504}

# 요청 간격은 도메인(호스트)별로 프로세스 전체가 공유한다. 사람인과 사람인(헤드헌팅)처럼 같은 사이트를
# 다른 크롤러(스레드)가 동시에 수집해도 그 사이트에는 간격을 지켜 하나씩 요청이 간다.
_host_locks: dict[str, threading.Lock] = {}
_host_last: dict[str, float] = {}
_registry_lock = threading.Lock()


class RobotsDisallowed(Exception):
    pass


@dataclass
class HttpSettings:
    user_agent: str
    delay_seconds: tuple[float, float] = (2.0, 4.0)  # 요청 사이 무작위 지연 범위
    timeout: float = 20.0
    max_retries: int = 3
    respect_robots: bool = True


class HttpClient:
    def __init__(self, settings: HttpSettings):
        self.settings = settings
        self._client = httpx.Client(
            headers={"User-Agent": settings.user_agent, "Accept-Language": "ko-KR,ko;q=0.9"},
            timeout=settings.timeout,
            follow_redirects=True,
        )
        self._robots: dict[str, RobotFileParser] = {}

    def close(self) -> None:
        self._client.close()

    def get_text(self, url: str, params: dict | None = None, headers: dict | None = None) -> str:
        return self._request("GET", url, params=params, headers=headers).text

    def get_json(self, url: str, params: dict | None = None, headers: dict | None = None) -> Any:
        return self._request("GET", url, params=params, headers=headers).json()

    def post_json(self, url: str, body: Any, headers: dict | None = None) -> Any:
        return self._request("POST", url, json=body, headers=headers).json()

    def _request(
        self, method: str, url: str, *, params: dict | None = None, json: Any = None, headers: dict | None = None
    ) -> httpx.Response:
        """요청 간격·재시도·robots.txt 확인을 거쳐 요청한다. params의 값이 리스트면 같은 키로 여러 번 보낸다."""
        full_url = str(self._client.build_request(method, url, params=params).url)
        if self.settings.respect_robots and not self._allowed(full_url):
            raise RobotsDisallowed(full_url)

        host = urlsplit(full_url).netloc
        for attempt in range(self.settings.max_retries + 1):
            self._wait(host)
            # 세션 쿠키가 있으면 검색 결과가 달라지는 사이트가 있어(사람인 2페이지부터) 매 요청을 무상태로 보낸다
            self._client.cookies.clear()
            try:
                resp = self._client.request(method, full_url, json=json, headers=headers)
                if resp.status_code not in RETRY_STATUS:
                    resp.raise_for_status()
                    return resp
                reason = f"HTTP {resp.status_code}"
            except httpx.TransportError as e:
                reason = repr(e)
            if attempt == self.settings.max_retries:
                raise RuntimeError(f"요청 실패 ({reason}): {full_url}")
            backoff = 2 ** attempt * 5
            log.warning("요청 실패 (%s), %ds 후 재시도: %s", reason, backoff, full_url)
            time.sleep(backoff)
        raise AssertionError("unreachable")

    def _wait(self, host: str) -> None:
        """같은 호스트에 대한 마지막 요청(어느 클라이언트든)으로부터 무작위 간격이 지날 때까지 기다린다."""
        with _registry_lock:
            lock = _host_locks.setdefault(host, threading.Lock())
        with lock:
            lo, hi = self.settings.delay_seconds
            remaining = _host_last.get(host, float("-inf")) + random.uniform(lo, hi) - time.monotonic()
            if remaining > 0:
                time.sleep(remaining)
            _host_last[host] = time.monotonic()

    def _allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        parser = self._robots.get(origin)
        if parser is None:
            parser = RobotFileParser()
            try:
                resp = self._client.get(origin + "/robots.txt")
                parser.parse(resp.text.splitlines() if resp.status_code == 200 else [])
            except httpx.HTTPError:
                log.warning("robots.txt를 가져오지 못함: %s", origin)
                parser.parse([])
            self._robots[origin] = parser
        return parser.can_fetch(self.settings.user_agent, url)
