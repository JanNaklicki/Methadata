"""E04: podobieństwo URL i przepływów sieciowych (HAR): host, wzorzec ścieżki, parametry, przekierowania 3xx,
identyfikatory integracji. Klucze, wartości i kolejność hostów liczone osobno."""
from .adapters import (EXTRACTOR_VERSION, METHOD_ID, UrlHarComparator, UrlHarExtractor, compare_features,
                       get_adapters)
from .har import detect_integrations, redirect_features
from .urlparts import parse_url

__all__ = ["UrlHarExtractor", "UrlHarComparator", "compare_features", "get_adapters", "parse_url",
           "detect_integrations", "redirect_features", "EXTRACTOR_VERSION", "METHOD_ID"]
