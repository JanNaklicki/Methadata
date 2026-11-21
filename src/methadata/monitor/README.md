# R04 – okresowy import nowych emisji

Interfejs dostawcy (`SourceProvider.fetch(cursor, limit) -> Page`) nad rekordami z S01 (Meta Ad Library).
Implementacje: `JsonFileProvider` (eksport JSON/JSONL z dysku, bez sieci) i `MetaAdLibraryProvider`
(Graph API `ads_archive`; token w `META_AD_LIBRARY_TOKEN`, wymaga zweryfikowanego konta dewelopera Meta).

Uruchomienie jednorazowe (cron/launchd, np. co godzinę; bez demona):

    python -m methadata.monitor run --store DIR --export EKSPORT [--config configs/monitor/monitor.json]
    python -m methadata.monitor run --store DIR --meta-search "fraza" --countries PL
    python -m methadata.monitor status --store DIR

Kod wyjścia 0 = sukces, 1 = błąd (kursor bez zmian, rekordy zapisane wcześniej zostają).

Semantyka: rekord = obserwacja `<source>:<id>` (artefakt `document` z JSON rekordu). Ten sam rekord = `duplicate`,
zmieniona treść = nowa wersja (`updated`). Kursor i kolejka analizy (`pending`) zapisują się atomowo w
`<store>/monitor/<nazwa>.state.json` po zapisie całej strony; przerwanie w środku strony powtarza ją idempotentnie.
Analiza (`payer_analyzer`: ekstraktor+komparator płatnika S01, komponenty spójne, runner F03) dostaje tylko
nowe/zmienione obserwacje, kiedy się nie uda, `pending` zostaje do następnego przebiegu. Historia: `*.runs.jsonl`.
Jeden przebieg naraz (flock). Ograniczenia: kursor pliku = offset (eksport tylko dopisywany); Meta po wyczerpaniu
wraca na początek (źródło od najnowszych, dedup po `id`); rekordy bez `id` są odrzucane.
