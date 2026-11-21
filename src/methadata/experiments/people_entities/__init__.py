"""E08: słownik aliasów osób, marek i produktów; rozpoznawanie nazw w HTML (i opcjonalnie OCR z E10); rola wzmianki
(wzmianka / podpis zdjęcia) oraz adnotacja osoby widocznej (obraz referencyjny po SHA-256) jako osobne typy dowodu."""
from .compare import (PeopleTextComparator, PeopleVisibleComparator, Mention, mentions, profile, visible_persons,  # noqa: F401
                      weighted_jaccard, NOT_DECISIVE)
from .extractor import EXTRACTOR_VERSION, PeopleEntitiesExtractor  # noqa: F401
from .lexicon import Lexicon, Hit, ReferenceImage, default_lexicon, fold, load_references  # noqa: F401


def get_adapters():
    return [PeopleEntitiesExtractor(), PeopleTextComparator(), PeopleTextComparator(
        method_id="people-text-persons-v1", types=("person",)), PeopleVisibleComparator()]
