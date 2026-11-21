# S01 – płatnik, beneficjent i emisja (Meta Ad Library)

Repozytorium: **Meta Ad Library** (Graph API `ads_archive`, eksport JSON; w UE z polami DSA `beneficiary_payers`).
Wejście: artefakt `document` (`application/json`) z jednym rekordem reklamy (obiekt albo `{"data":[rekord]}`).

| Rola | Pole źródła (kolejność awaryjna) | Cecha (`family=payer`) |
|---|---|---|
| płatnik | `beneficiary_payers[].payer` -> `funding_entity` -> `bylines` ("Paid for by ...") | `payer_name` |
| beneficjent (podmiot promowany) | `beneficiary_payers[].beneficiary` | `beneficiary_name` |
| reklamodawca (strona publikująca) | `page_name` (+ `page_id`) | `advertiser_name`, `advertiser_id` |
| ID emisji | `id` / `ad_archive_id` | `ad_id` |
| okres | `ad_delivery_start_time`, `ad_delivery_stop_time` (brak stopu = trwa) | `delivery_start`, `delivery_stop` |
| pochodzenie | repozytorium, użyte pola, `capture_time` | `record_provenance` (json) |

Płatnik, beneficjent i reklamodawca są trzema osobnymi rolami: agencja bywa płatnikiem wielu klientów
(beneficjentów), a reklamodawca (strona) nie jest płatnikiem. Pole jest deklaracją reklamodawcy, nie weryfikacją.
Gdy źródło pola nie ujawnia, cechy nie powstają (`ExtractionStatus` ok z opisem braku).

Porównanie (`payer-compare`, relacja `campaign_candidate`): ten sam płatnik i ten sam beneficjent = 1.0;
ten sam płatnik, różni beneficjenci (agencja) = 0.4; ten sam beneficjent, inny płatnik = 0.7; różne = 0.0;
brak płatnika po którejkolwiek stronie = `score=None`. `group_by_payer` grupuje kreacje wg znormalizowanego płatnika.

Aktualizacja: eksport ponownie pobiera się z API (`ads_archive?search_terms=...&fields=...`) i importuje;
zmiana treści rekordu = nowy artefakt/obserwacja; moduł nie wykonuje zapytań sieciowych.
