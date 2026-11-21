# S03: kontakt handlowy i konto komunikatora

Adaptery `source-contact-extractor` i `source-contact-compare` (entry point `source-contact`, konfiguracja: `configs/contact/contact.json`).

- Wejście: artefakty HTML (skrypty/style pomijane, nic nie jest wykonywane), tekst OCR (artefakt `text/plain` albo `optional["ocr_texts"]`).
- Cechy (`family=contact`): `contact_phone`, `contact_email`, `contact_messenger`, `contact_brand`. Wartość JSON: `key` (HMAC-SHA256 znormalizowanej wartości kluczem lokalnym), `key_id` (8 znaków skrótu klucza; cechy z różnymi `key_id` nie są porównywane), `masked`, `role` (order/hotline/support/general), `country`, `platform`, `source` (html/ocr), `captured_at`, `status` (ok/low_confidence), `provenance`. Jawny numer/adres nie jest zapisywany.
- Normalizacja: telefon do E.164 (domyślnie PL, prefiksy +/00, separatory, korekta O/l w OCR oznaczona `low_confidence`), e-mail (małe litery, bez `+tag`, kropki w gmail, `[at]`/`[dot]`), konto = platforma + id (wa.me, t.me, m.me, viber, signal, line).
- Porównanie: ten sam kontakt zamówieniowy pod różnymi markami 0.9, pod tą samą marką 0.8, wspólna infolinia/pośrednik 0.35 (mylące), brak kontaktu: `score=None`.
- Ograniczenia: klucz: `METHADATA_PII_KEY`, inaczej plik `pii.key` (0600) w `METHADATA_PII_KEY_DIR` lub katalogu tymczasowym, tworzony raz; utrata klucza = brak porównań ze starymi cechami, wyciek klucza + znajomość przestrzeni numerów umożliwia odtworzenie; marka z `og:site_name`/`<title>`; kody krajów w ograniczonej tabeli.
