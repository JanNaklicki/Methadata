"""Jawnie uruchamiane pobranie publicznego HTTP(S): limity, ręczne przekierowania, ochrona SSRF.
Klient: stdlib http.client (bez proxy z środowiska, bez wykonywania treści). Połączenie jest przypięte do
sprawdzonego adresu IP (brak DNS rebinding), a każdy hop przekierowania jest walidowany od nowa."""
from __future__ import annotations

import http.client
import socket
import ssl
import time
from dataclasses import dataclass, field
from typing import Callable, Optional
from urllib.parse import urljoin, urldefrag

from .urlcheck import BlockedTarget, Resolver, check_target

REDIRECT_CODES = {301, 302, 303, 307, 308}
USER_AGENT = "MethadataIntake/1.0 (+research; no-script-execution)"


@dataclass(frozen=True)
class FetchPolicy:
    timeout_s: float = 10.0          # budżet całego pobrania (łącznie z przekierowaniami)
    max_bytes: int = 10 * 1024 * 1024
    max_redirects: int = 5
    allow_private: bool = False      # tylko tryb testowy


@dataclass
class Hop:
    url: str
    status: int
    location: Optional[str] = None

    def to_dict(self):
        return dict(self.__dict__)


@dataclass
class FetchResult:
    url: str
    final_url: str
    fetch_status: str  # ok | http_error | blocked_target | timeout | too_large | too_many_redirects | network_error
    http_status: Optional[int] = None
    error: Optional[str] = None
    redirects: list[Hop] = field(default_factory=list)
    body: Optional[bytes] = None
    content_type: Optional[str] = None
    started_at: str = ""
    duration_s: float = 0.0

    @property
    def fetched(self) -> bool:
        return self.fetch_status in ("ok", "http_error") and self.body is not None


class _Timeout(Exception):
    pass


class _TooLarge(Exception):
    pass


class _Conn(http.client.HTTPConnection):
    def __init__(self, host, port, ip, tls, timeout, dial):
        super().__init__(host, port, timeout=timeout)
        self._ip, self._tls, self._dial = ip, tls, dial

    def connect(self):
        sock = self._dial(self._ip, self.port, self.timeout)
        if self._tls:
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=self.host)
        self.sock = sock


def _dial(ip, port, timeout):
    return socket.create_connection((ip, port), timeout=timeout)


class Fetcher:
    def __init__(self, policy: FetchPolicy = FetchPolicy(), *, resolver: Optional[Resolver] = None,
                 dial: Callable = _dial):
        self.policy, self.resolver, self.dial = policy, resolver, dial

    def fetch(self, url: str) -> FetchResult:
        t0 = time.monotonic()
        res = FetchResult(url=url, final_url=url, fetch_status="network_error",
                          started_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        deadline = t0 + self.policy.timeout_s
        try:
            self._run(res, deadline)
        except BlockedTarget as e:
            res.fetch_status, res.error = "blocked_target", f"{e.code}: {e}"
        except (_Timeout, socket.timeout, TimeoutError):
            res.fetch_status, res.error = "timeout", f"przekroczono {self.policy.timeout_s}s"
        except _TooLarge as e:
            res.fetch_status, res.error, res.body = "too_large", str(e), None
        except (OSError, http.client.HTTPException, ssl.SSLError, UnicodeError, ValueError) as e:
            res.fetch_status, res.error = "network_error", f"{type(e).__name__}: {e}"
        res.duration_s = round(time.monotonic() - t0, 3)
        return res

    def _run(self, res: FetchResult, deadline: float) -> None:
        cur = urldefrag(res.url.strip())[0]
        for _ in range(self.policy.max_redirects + 1):
            host, port, ip, scheme = check_target(cur, allow_private=self.policy.allow_private,
                                                  resolver=self.resolver)
            res.final_url = cur
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise _Timeout()
            conn = _Conn(host, port, ip, scheme == "https", remaining, self.dial)
            try:
                path = cur.split("://", 1)[1]
                path = "/" + path.split("/", 1)[1] if "/" in path else "/"
                conn.request("GET", path, headers={"User-Agent": USER_AGENT, "Accept": "*/*",
                                                   "Accept-Encoding": "identity", "Connection": "close"})
                r = conn.getresponse()
                loc = r.getheader("Location")
                if r.status in REDIRECT_CODES and loc:
                    res.redirects.append(Hop(cur, r.status, loc))
                    cur = urldefrag(urljoin(cur, loc))[0]
                    continue
                res.http_status = r.status
                res.content_type = r.getheader("Content-Type")
                cl = r.getheader("Content-Length")
                if cl and cl.isdigit() and int(cl) > self.policy.max_bytes:
                    raise _TooLarge(f"Content-Length {cl} > {self.policy.max_bytes}")
                res.body = self._read(r, deadline, conn.sock)
                res.fetch_status = "ok" if 200 <= r.status < 300 else "http_error"
                if res.fetch_status == "http_error":
                    res.error = f"HTTP {r.status}"
                return
            finally:
                conn.close()
        res.fetch_status, res.error = "too_many_redirects", f"więcej niż {self.policy.max_redirects} przekierowań"

    def _read(self, r, deadline: float, sock) -> bytes:
        buf = bytearray()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise _Timeout()
            if sock is not None:
                sock.settimeout(remaining)
            chunk = r.read1(65536)
            if not chunk:
                return bytes(buf)
            buf += chunk
            if len(buf) > self.policy.max_bytes:
                raise _TooLarge(f"treść przekroczyła {self.policy.max_bytes} B")
