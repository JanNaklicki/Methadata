import methadata_api.enrichment as enrichment


def test_private_and_malformed_hosts_get_no_outgoing_requests(tmp_path, monkeypatch):
    monkeypatch.delenv("METHADATA_DISABLE_ENRICHMENT", raising=False)
    touched = []
    monkeypatch.setattr(enrichment, "_dns_ips", lambda host, timeout=2.0: {
        "internal.example.com": ["10.0.0.5"], "metadata.example.com": ["169.254.169.254"],
        "dead.example.com": [], "scam.example.net": ["93.184.216.34"],
    }.get(host, []))
    monkeypatch.setattr(enrichment, "_tls_info", lambda host, ip, timeout=2.5: touched.append(("tls", host, ip)) or {"issuer": "CA"})
    monkeypatch.setattr(enrichment, "_rdap_info", lambda domain, timeout=3.0: touched.append(("rdap", domain)) or {"registrar": "R"})

    result = enrichment.enrich_domains(
        ["127.0.0.1", "localhost", "169.254.169.254", "internal.example.com", "metadata.example.com",
         "dead.example.com", "scam.example.net"], tmp_path / "cache.json")

    # Do sieci wychodzą tylko: TLS do zweryfikowanego publicznego IP oraz RDAP po samej nazwie domeny.
    assert ("tls", "scam.example.net", "93.184.216.34") in touched
    assert not any(item[0] == "tls" and item[1] != "scam.example.net" for item in touched)
    assert not any(item[0] == "rdap" and item[1] in {"internal.example.com", "metadata.example.com"} for item in touched)
    assert result["scam.example.net"]["tls_issuer"] == "CA"
    assert result["dead.example.com"]["registrar"] == "R"   # martwa domena nadal ma publiczne metadane RDAP
    assert "internal.example.com" not in result and "metadata.example.com" not in result
    assert "127.0.0.1" not in result and "localhost" not in result
