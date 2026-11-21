"""Filtry „biblioteki i szablon przeglądarki”: co odjąć, by zostały elementy własne zestawu.

Deklaratywne wzorce (konfiguracja modułu E12); są jawne, żeby wynik dało się zweryfikować i rozszerzyć."""
from __future__ import annotations

import re
from typing import Optional

# Gotowe biblioteki: nazwa pliku/URL zasobu (js/css).
LIBRARY_URL = re.compile(
    r"(?:^|[/._-])(jquery(?:[.-]ui|[.-]migrate|[.-]mobile)?|bootstrap|popper|fontawesome|font-awesome|fa-(?:solid|brands|regular)|"
    r"normalize|reset|animate|swiper|slick|owl[.-]carousel|lightbox|fancybox|moment|lodash|underscore|backbone|angular|react(?:-dom)?|"
    r"vue|axios|gsap|aos|wow|modernizr|html5shiv|respond|select2|datatables|chart|d3|three|polyfill|core-js|"
    r"gtag|analytics|gtm|fbevents|hotjar|recaptcha|cloudflare|cdn-cgi|wp-emoji|wp-includes|wp-content/plugins|elementor|"
    r"materialize|bulma|foundation|tailwind|uikit|semantic|mdb|swal|sweetalert|toastr|clipboard)(?:[.\-_/@0-9]|$)", re.I)

# Klasy frameworków i CMS-ów (prefiksy/nazwy dokładne).
LIBRARY_CLASS = re.compile(
    r"^(?:(?:col|row|container|btn|navbar|nav|card|modal|form-control|form-group|input-group|alert|badge|dropdown|carousel|"
    r"collapse|d-flex|d-none|d-block|text-(?:center|left|right|muted|white)|bg-|mt-|mb-|ml-|mr-|mx-|my-|pt-|pb-|pl-|pr-|px-|py-|"
    r"fa|fas|far|fab|fal|fa-[a-z0-9-]+|glyphicon|owl-|slick-|swiper-|ui-|wp-|elementor-|et_|vc_|"
    r"UFI[\w-]*|_[0-9a-z_-]{2,5}|fb[-_][\w-]+|fb_[\w-]+|twitter-[\w-]+|"
    r"clearfix|pull-(?:left|right)|hidden|visible|show|active|disabled|fade|in|open|container-fluid|"
    r"(?:xs|sm|md|lg|xl)-\d+)(?:[-_].*)?|js-?focus-visible|no-js|js)$", re.I)

# Klasy narzędziowe (Tailwind i podobne): wspólne dla wielu niezwiązanych stron.
UTILITY_CLASS = re.compile(
    r"^(?:[a-z0-9]+:)*-?(?:absolute|relative|fixed|sticky|static|block|inline(?:-block|-flex|-grid)?|flex(?:-[\w]+)?|grid(?:-[\w]+)?|"
    r"hidden|visible|invisible|container|truncate|uppercase|lowercase|capitalize|italic|underline|"
    r"(?:bg|text|p[xytblr]?|m[xytblr]?|w|h|min-w|min-h|max-w|max-h|rounded[a-z]*|border[a-z]*|shadow|justify|items|self|gap|space-[xy]|"
    r"font|leading|tracking|z|top|bottom|left|right|inset|overflow|opacity|object|cursor|transition|duration|ease|transform|scale|"
    r"rotate|translate|from|to|via|order|col-span|row-span|ring|fill|stroke|divide|align|float|clear|sr)(?:-[\w./\[\]%#-]+)?)$", re.I)

# Elementy wstrzykiwane przez przeglądarkę/rozszerzenia/translatory (nie należą do zestawu strony).
BROWSER_CLASS = re.compile(
    r"^(?:neterror|interstitial-wrapper|error-code|icon-offline|offline|nav-wrapper|translated-(?:ltr|rtl)|goog-[\w-]+|skiptranslate|"
    r"VIpgJd-[\w-]+|grammarly[\w-]*|gr_[\w-]*|lastpass[\w-]*|__[a-z]*-?(?:extension|ext)[\w-]*|chrome-[\w-]+|moz-[\w-]+|"
    r"firefox-[\w-]+|webkit-[\w-]+|safari-[\w-]+|reader-[\w-]+|cf-[\w-]+|cloudflare-[\w-]+|adblock[\w-]*|pdf-viewer|"
    r"kasperskylab_[\w-]+|avast[\w-]*)$", re.I)
BROWSER_URL = re.compile(r"^(?:chrome-extension|moz-extension|chrome|about|resource|safari-extension|edge)://|chrome-error://", re.I)


def is_library_url(url: Optional[str]) -> bool:
    return bool(url) and bool(LIBRARY_URL.search(url))


def is_browser_url(url: Optional[str]) -> bool:
    return bool(url) and bool(BROWSER_URL.search(url))


def class_origin(name: str) -> Optional[str]:
    """'browser' | 'library' | None (klasa własna)."""
    if BROWSER_CLASS.match(name):
        return "browser"
    if LIBRARY_CLASS.match(name) or UTILITY_CLASS.match(name):
        return "library"
    return None
