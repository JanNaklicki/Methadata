# Backend Methadata (W02)

Start samodzielny (port 8000, tylko lokalnie):

    pip install -e '.[web]'
    METHADATA_DATA_DIR=var/methadata-data python -m methadata_api

Zmienne: `METHADATA_DATA_DIR` (magazyn, domyślnie `var/methadata-data`), `METHADATA_HOST`, `METHADATA_PORT`,
`METHADATA_WORKERS`, `METHADATA_BASELINE_CONFIG`, `METHADATA_MAX_UPLOAD_BYTES` (limit przesyłanego pliku, domyślnie 52428800 = 50 MiB zgodnie z kontraktem). Zestaw demo (syntetyczny, `backend/methadata_api/demo_data`) jest
seedowany idempotentnie przy starcie; działa bez BACKUP, odszyfrowanych linków i kluczy.

Test zgodności: `python contracts/http/check_conformance.py --base-url http://localhost:8000`.
Testy integracyjne: `pytest tests/backend`.
