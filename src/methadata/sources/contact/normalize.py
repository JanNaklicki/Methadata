"""S03: normalizacja i maskowanie kontaktów (telefon, e-mail, konto komunikatora).

Wartość jawna nie opuszcza ekstraktora: cechy niosą tylko znormalizowany skrót (`key`) do porównań i maskę (`masked`).
"""
from __future__ import annotations

import hashlib
import re
from typing import Optional
from urllib.parse import parse_qs, unquote, urlparse

# kod kraju -> (ISO, dopuszczalne długości numeru krajowego)
CALLING: dict[str, tuple[str, tuple[int, ...]]] = {
    "48": ("PL", (9,)), "49": ("DE", (9, 10, 11)), "420": ("CZ", (9,)), "421": ("SK", (9,)), "44": ("GB", (10,)),
    "1": ("US", (10,)), "380": ("UA", (9,)), "370": ("LT", (8,)), "371": ("LV", (8,)), "372": ("EE", (7, 8)),
    "43": ("AT", (10, 11)), "33": ("FR", (9,)), "39": ("IT", (9, 10)), "34": ("ES", (9,)), "31": ("NL", (9,)),
    "32": ("BE", (8, 9)), "41": ("CH", (9,)), "36": ("HU", (8, 9)), "40": ("RO", (9,)), "359": ("BG", (8, 9)),
    "7": ("RU", (10,)), "90": ("TR", (10,)), "353": ("IE", (9,)), "46": ("SE", (9,)), "47": ("NO", (8,)),
    "45": ("DK", (8,)), "358": ("FI", (9, 10)),
}
_REGION_CODE = {v[0]: k for k, v in CALLING.items()}
_OCR_DIGITS = str.maketrans({"O": "0", "o": "0", "I": "1", "l": "1"})


def digest(kind: str, value: str, salt: str = "methadata-contact-v1") -> str:
    return hashlib.sha256(f"{salt}|{kind}|{value}".encode("utf-8")).hexdigest()[:20]


def normalize_phone(raw: str, default_region: str = "PL", ocr: bool = False,
                    explicit_context: bool = False) -> Optional[tuple[str, str, bool]]:
    """-> (E.164, ISO kraju|'', poprawiono_OCR) albo None. Bez prefiksu międzynarodowego: tylko numer krajowy regionu domyślnego."""
    s = raw.strip()
    fixed = False
    if ocr:
        t = s.translate(_OCR_DIGITS)
        fixed = t != s and any(c in s for c in "OolI")
        s = t
    s = re.sub(r"^[\s(]+", "", s)
    s = re.sub(r"^(\+|00)\s*\)", r"\1", s)
    s = re.sub(r"^\(\s*(\+|00)", r"\1", s)
    plus = s.startswith("+") or s.startswith("00")
    digits = re.sub(r"\D", "", s)
    if s.startswith("00"):
        digits = digits[2:]
    if not digits or len(set(digits)) == 1:
        return None
    if plus:
        for n in (3, 2, 1):
            cc = digits[:n]
            if cc in CALLING:
                iso, lens = CALLING[cc]
                nat = digits[n:]
                if len(nat) in lens and not nat.startswith("0"):
                    return "+" + digits, iso, fixed
                return None
        return ("+" + digits, "", fixed) if 8 <= len(digits) <= 15 else None
    cc = _REGION_CODE.get(default_region)
    if not cc:
        return None
    lens = CALLING[cc][1]
    if len(digits) in lens and not digits.startswith("0"):
        grouped = bool(re.search(r"[\s().\- ]", s))
        if grouped or explicit_context:
            return "+" + cc + digits, default_region, fixed
    return None


def mask_phone(e164: str) -> str:
    d = e164[1:]
    cc = next((c for c in (d[:3], d[:2], d[:1]) if c in CALLING), d[:2])
    return f"+{cc} " + "•" * max(len(d) - len(cc) - 3, 3) + " " + d[-3:]


_AT = re.compile(r"\s*[\[(]\s*(?:at|małpa|malpa|@)\s*[\])]\s*", re.I)
_DOT = re.compile(r"\s*[\[(]\s*(?:dot|kropka)\s*[\])]\s*", re.I)
EMAIL_RE = re.compile(r"(?<![\w.+-])[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}(?![\w-])")
_BAD_TLD = {"png", "jpg", "jpeg", "gif", "webp", "svg", "css", "js", "ico", "woff", "woff2"}


def deobfuscate(text: str) -> str:
    return _DOT.sub(".", _AT.sub("@", text))


def normalize_email(raw: str) -> Optional[tuple[str, str, str]]:
    """-> (adres znormalizowany, lokalna, domena) albo None (np. 'img@2x.png')."""
    s = raw.strip().strip(".,;:<>()[]\"'").lower()
    if s.startswith("mailto:"):
        s = s[7:].split("?")[0]
    if s.count("@") != 1:
        return None
    local, dom = s.split("@")
    if not local or not dom or dom.rsplit(".", 1)[-1] in _BAD_TLD or ".." in s:
        return None
    local = local.split("+")[0]
    if dom in ("gmail.com", "googlemail.com"):
        dom, local = "gmail.com", local.replace(".", "")
    return (f"{local}@{dom}", local, dom) if local else None


def mask_email(local: str, dom: str) -> str:
    head, _, tld = dom.partition(".")
    return f"{local[0]}•••@{head[0]}•••.{tld}"


def _clean_user(u: str) -> str:
    return unquote(u).strip().lstrip("@~").strip("/").lower()


def parse_messenger_url(url: str, default_region: str = "PL") -> Optional[tuple[str, str]]:
    """-> (platforma, identyfikator) z odnośnika komunikatora; numery sprowadzone do E.164."""
    u = url.strip()
    if re.match(r"^(viber|tg|whatsapp|signal)://", u, re.I):
        p = urlparse(u)
        q = parse_qs(p.query)
        plat = {"viber": "viber", "tg": "telegram", "whatsapp": "whatsapp", "signal": "signal"}[p.scheme.lower()]
        num = (q.get("number") or q.get("phone") or [""])[0]
        if plat == "telegram":
            d = (q.get("domain") or [""])[0]
            return ("telegram", _clean_user(d)) if d else None
        r = normalize_phone(num if num.startswith("+") else "+" + num.lstrip("+"), default_region, explicit_context=True)
        return (plat, r[0]) if r else None
    if not re.match(r"^https?://", u, re.I):
        u = "https://" + u
    p = urlparse(u)
    host = (p.hostname or "").lower().removeprefix("www.")
    path = p.path
    q = parse_qs(p.query)
    if host in ("wa.me", "api.whatsapp.com", "web.whatsapp.com", "chat.whatsapp.com"):
        num = path.strip("/") if host == "wa.me" else (q.get("phone") or [""])[0]
        r = normalize_phone("+" + re.sub(r"\D", "", num), default_region, explicit_context=True) if num else None
        return ("whatsapp", r[0]) if r else None
    if host in ("t.me", "telegram.me", "telegram.dog"):
        user = path.strip("/").split("/")[0]
        return ("telegram", _clean_user(user)) if user and user not in ("joinchat", "share", "addstickers") else None
    if host in ("m.me", "messenger.com"):
        user = path.strip("/").split("/")[-1]
        return ("messenger", _clean_user(user)) if user and user != "t" else None
    if host in ("facebook.com", "fb.com", "fb.me") and "/messages/t/" in path:
        return ("messenger", _clean_user(path.split("/messages/t/")[1].split("/")[0]))
    if host == "signal.me":
        m = re.search(r"#p/(\+?\d+)", u)
        r = normalize_phone("+" + m.group(1).lstrip("+"), default_region, explicit_context=True) if m else None
        return ("signal", r[0]) if r else None
    if host in ("line.me", "lin.ee"):
        m = re.search(r"/ti/p/(~?[\w.-]+)", path)
        return ("line", _clean_user(m.group(1))) if m else None
    if host in ("viber.com", "invite.viber.com"):
        num = (q.get("number") or [""])[0]
        r = normalize_phone("+" + re.sub(r"\D", "", num), default_region, explicit_context=True) if num else None
        return ("viber", r[0]) if r else None
    return None


def mask_account(platform: str, ident: str) -> str:
    if ident.startswith("+"):
        return platform + ":" + mask_phone(ident)
    return f"{platform}:{ident[:2]}" + "•" * max(len(ident) - 2, 2)
