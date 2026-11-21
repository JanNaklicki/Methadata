"""E12: wspólny zestaw kodu i komponentów strony (arkusze CSS, rzadkie klasy, skrypty lokalne, budowa formularza)."""
from .compare import (METHOD_ALL, METHOD_NOLIB, Corpus, ProductionKitComparator, ProductionKitNoLibComparator,
                      shared_elements)
from .elements import CLASS, CSS_SHEET, FORM, SCRIPT, Element, extract_elements, form_signature
from .extractor import EXTRACTOR_VERSION, KitExtractor


def get_adapters():
    """Komparatory bez korpusu zwracają score=None; runner powinien zbudować je przez .fit(observations, feature_map)."""
    return [KitExtractor(), ProductionKitComparator(), ProductionKitNoLibComparator()]


__all__ = ["KitExtractor", "ProductionKitComparator", "ProductionKitNoLibComparator", "Corpus", "Element", "extract_elements",
           "form_signature", "shared_elements", "get_adapters", "METHOD_ALL", "METHOD_NOLIB", "EXTRACTOR_VERSION",
           "CSS_SHEET", "CLASS", "SCRIPT", "FORM"]
