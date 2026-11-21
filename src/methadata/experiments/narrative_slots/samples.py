"""Próbki kontrolne SYNTETYCZNE (nie dane z korpusu): ta sama rama z inną osobą oraz ta sama osoba w innym kontekście."""
from __future__ import annotations

FRAME = (
    "<html><head><title>PILNE: Brutalna napaść na {acc} tuż po publikacji śledztwa</title></head><body><article>"
    "<p>Dziennikarz złożył oświadczenie przed szpitalem. Wczoraj wieczorem na {acc} dokonano napaści na ulicy. "
    "{nom} szedł do samochodu, gdy trzech mężczyzn przewróciło go na ziemię.</p>"
    "<p>Redakcja {org} przeprowadziła weryfikację platformy {prod}. Pieniądze pozostają na koncie użytkownika, "
    "wypłata w ciągu 24 godzin.</p><p>Zyski są wielokrotnie wyższe niż z lokat bankowych.</p>"
    "<p>Dopóki dostęp nie zostanie zamknięty, link do rejestracji pozostaje otwarty.</p></article>"
    "<button>SPRAWDŹ, JAK UZYSKAĆ DOSTĘP</button></body></html>")

OTHER_CONTEXT = (
    "<html><head><title>{nom} poleca aplikację do planowania podróży</title></head><body><article>"
    "<p>Dziennikarz opowiada o planach na sezon i o pracy redakcji {org}. {nom} przyznaje, że aplikacja Podróżnik "
    "jest bezpłatna i oszczędza czas przy rezerwacjach.</p></article>"
    "<button>Pobierz aplikację</button></body></html>")

PERSON_A = {"nom": "Krzysztof Stanowski", "acc": "Krzysztofa Stanowskiego", "org": "Kanału Zero", "prod": "Świtowin Zyskarnia"}
PERSON_B = {"nom": "Tomasz Sekielski", "acc": "Tomasza Sekielskiego", "org": "TVN24", "prod": "Steryga Dochodnica"}
SWAP_A_TO_B = {"Krzysztof Stanowski": "Tomasz Sekielski", "Krzysztofa Stanowskiego": "Tomasza Sekielskiego",
               "Kanału Zero": "TVN24", "Świtowin Zyskarnia": "Steryga Dochodnica"}


def pages() -> dict[str, str]:
    return {"frame-A": FRAME.format(**PERSON_A), "frame-B": FRAME.format(**PERSON_B),
            "other-A": OTHER_CONTEXT.format(**PERSON_A)}
