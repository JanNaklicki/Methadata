# S07 — pochodzenie materiału (C2PA)

Adaptery: `source-media-provenance-extractor`, `source-media-provenance-compare` (entry point `source-media-provenance`).
Extra: `pip install -e '.[provenance]'` (c2pa-python 0.39 / c2pa-rs 0.92, spec C2PA 2.4; działa na Python 3.14).
Bez extra działa ograniczony parser JUMBF (JPEG APP11, PNG caBX): wykrywa manifest i etykiety manifestów,
bez walidacji (`Unverified`, wynik porównania ograniczony do 0.6).

Cechy (family `provenance`): `c2pa_present`, `c2pa_validation_state` (Valid/Trusted/Invalid), `c2pa_validation`
(kody błędów, wersje walidatora), `c2pa_active_manifest`, `c2pa_ancestor_manifests` (identyfikatory składników),
`c2pa_ingredients`, `c2pa_signer`, `c2pa_signed_time`, `c2pa_actions`, `record_provenance`.
Brak manifestu: `c2pa_present=false` -> porównanie `score=None` (brak danych). Walidacja bez listy zaufania daje `Valid`
(kod `signingCredential.untrusted`), nie `Trusted`.
Porównanie: pochodna 1.0, wspólny przodek 0.9, Invalid <=0.3, Unverified <=0.6, brak wspólnego składnika -> None.
Nieprzetestowane: wideo (MP4) i WebP/HEIC (obsługiwane przez bibliotekę, brak próbek), listy zaufania, TSA.
