"""Domyślny leksykon reguł E07 (polski). Zamrożona kopia: configs/narrative_slots/default.json (test pilnuje zgodności)."""
from __future__ import annotations

LEXICON: dict = {
    "version": "narrative-slots-rules-1",
    # klasy zdarzeń: wzorce na małych literach (prefiksy fleksyjne)
    "event": {
        "attack": ["napa(ść|sc|d)\\w*", "napadn\\w*", "zaatak\\w*", "pobit\\w*", "pobici\\w*", "\\batak\\w*", "zamach\\w*", "strzel\\w*", "strzał\\w*"],
        "arrest": ["aresztow\\w*", "zatrzyman\\w*", "zarzut\\w*", "wyrok\\w*"],
        "death": ["zmarł\\w*", "zginął\\w*", "śmierć\\w*", "nie żyje"],
        "scandal": ["skandal\\w*", "afer\\w*", "ujawni\\w*", "wyciek\\w*", "zdemaskow\\w*"],
        "decision": ["decyzj\\w*", "ustaw\\w*", "wprowadz\\w*", "zakaz\\w*", "oświadczy\\w*"],
    },
    "profession": ["dziennikar", "polityk", "lekarz", "aktor", "piosenk", "premier", "prezydent", "minister",
                   "ekonomist", "ekspert", "prezenter", "celebryt", "biznesmen", "naukowiec", "piłkarz", "trener",
                   "prezes", "wokalist", "komik", "youtuber"],
    "promise": {
        "return": ["zysk\\w*", "doch[óo]d\\w*", "zarob\\w*", "zarób\\w*", "zarabi\\w*", "pasywn\\w*",
                   "wypłac\\w*", "wypłat\\w*"],
        "guarantee": ["gwarant\\w*", "bez ryzyka", "bezpiecz\\w*"],
        "free": ["za darmo", "bezpłatn\\w*", "gratis"],
        "savings": ["oszczęd\\w*", "odsetk\\w*", "taniej", "rabat\\w*"],
    },
    "promise_weak": ["\\d+\\s?%", "\\d[\\d\\s]*\\s?zł", "miesięcznie", "dziennie", "24 godzin\\w*", "jednorazow\\w*", "bonus\\w*"],
    "product_nouns": {"platform": "platform", "aplikacj": "app", "system": "system", "program": "program", "robot": "robot",
                      "kurs": "course", "metod": "method", "fundusz": "fund", "narzędzi": "tool"},
    "product_generic": ["sztuczn\\w+ inteligencj\\w*", "algorytm\\w*", "\\bai\\b"],
    "call": {
        "get_access": ["rejestr\\w*", "zarejestruj\\w*", "dostęp\\w*", "uzyska\\w*", "link\\w*", "sprawdź", "dołącz\\w*",
                       "załóż", "zapisz\\w*", "kliknij", "wejdź"],
        "purchase": ["kup\\w*", "zamów\\w*", "zapłać"],
        "claim": ["odbierz", "pobierz", "skorzystaj"],
    },
    "call_urgency": ["jeszcze \\d+ godzin\\w*", "pozostaje otwart\\w*", "ostatni\\w*", "tylko dziś", "zamkn\\w*", "ogranicz\\w*"],
    "stop_names": ["AI", "VIP", "TV", "OK", "PILNE", "REKLAMA", "NOTA", "RODO", "CTA", "PLN", "EUR", "USD", "SMS", "PDF"],
    "stop_name_stems": ["polsk", "polsc", "europ", "warsza", "unia", "unii", "gazeta", "gazet"],
    "generic_name_stems": ["redakc", "nota", "minist", "urząd", "bank", "gazeta", "kancel"],
    "promise_penalty": ["lokat\\w*", "prowizj\\w*"],
    "stop_tokens": ["oraz", "który", "która", "które", "który", "jest", "się", "ale", "dla", "przez", "jako", "tego", "tym",
                    "jego", "jej", "ich", "gdy", "lub", "też", "nie", "już", "tylko", "bez", "pod", "nad", "przy", "tak",
                    "jak", "czy", "ten", "ta", "to", "te", "tej", "tych", "tuż", "po"],
    "max_quote_chars": 240,
}
