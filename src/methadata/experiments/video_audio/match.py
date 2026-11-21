"""Dopasowanie sekwencyjne (czyste funkcje; numpy tylko w dopasowaniu audio): fragment krótszej kreacji w dłuższej."""
from __future__ import annotations

import re
from typing import Any, Mapping, Optional, Sequence

from methadata.experiments.perceptual import phash as PH


def frame_run(fa: Sequence[Mapping[str, Any]], fb: Sequence[Mapping[str, Any]], cfg: Mapping[str, Any]
              ) -> Optional[dict[str, Any]]:
    """Najdłuższy wspólny fragment na przekątnej (te same odstępy próbkowania), tolerancja `gap_tolerance` klatek.
    Zwraca {"a":(t0,t1),"b":(t0,t1),"frames","hits","mean_hamming"} lub None."""
    thr, gap, interval = cfg["hash_threshold"], int(cfg["gap_tolerance"]), int(cfg["frame_interval_ms"])
    ha = [None if f["phash"] is None else PH.from_hex(f["phash"]) for f in fa]
    hb = [None if f["phash"] is None else PH.from_hex(f["phash"]) for f in fb]
    best = None
    for d in range(-(len(ha) - 1), len(hb)):
        i0 = max(0, -d)
        run_start, last_hit, hits, ham, miss = None, None, 0, 0, 0
        for i in range(i0, min(len(ha), len(hb) - d)):
            x, y = ha[i], hb[i + d]
            hit = x is not None and y is not None and PH.hamming(x, y) <= thr
            if hit:
                if run_start is None:
                    run_start, hits, ham = i, 0, 0
                hits += 1
                ham += PH.hamming(x, y)
                last_hit, miss = i, 0
            elif run_start is not None:
                miss += 1
                if miss > gap:
                    best = _better(best, run_start, last_hit, d, hits, ham)
                    run_start = None
        if run_start is not None:
            best = _better(best, run_start, last_hit, d, hits, ham)
    if best is None:
        return None
    n, i0, i1, d, hits, ham = best
    return {"a": (fa[i0]["time_ms"], fa[i1]["time_ms"] + interval),
            "b": (fb[i0 + d]["time_ms"], fb[i1 + d]["time_ms"] + interval),
            "frames": i1 - i0 + 1, "hits": hits, "mean_hamming": round(ham / max(1, hits), 2),
            "index_a": i0, "index_b": i0 + d}


def _better(best, i0, i1, d, hits, ham):
    cand = (hits, i0, i1, d, hits, ham)
    if best is None or (cand[0], -cand[3] ** 2) > (best[0], -best[3] ** 2):
        return cand
    return best


def shot_match(sa: Sequence[Mapping[str, Any]], sb: Sequence[Mapping[str, Any]], cfg: Mapping[str, Any]
               ) -> Optional[dict[str, Any]]:
    """Wspólne ujęcia w tej samej kolejności (LCS po hashu ujęcia) - odporne na wycięte ujęcia (skrócony montaż)."""
    thr = cfg["hash_threshold"]
    ha = [None if s["phash"] is None else PH.from_hex(s["phash"]) for s in sa]
    hb = [None if s["phash"] is None else PH.from_hex(s["phash"]) for s in sb]
    n, m = len(ha), len(hb)

    def eq(i, j):
        return ha[i] is not None and hb[j] is not None and PH.hamming(ha[i], hb[j]) <= thr
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            dp[i][j] = dp[i + 1][j + 1] + 1 if eq(i, j) else max(dp[i + 1][j], dp[i][j + 1])
    pairs, i, j = [], 0, 0
    while i < n and j < m:
        if eq(i, j) and dp[i][j] == dp[i + 1][j + 1] + 1:
            pairs.append((i, j))
            i += 1
            j += 1
        elif dp[i + 1][j] >= dp[i][j + 1]:
            i += 1
        else:
            j += 1
    if len(pairs) < int(cfg["min_shot_matches"]):
        return None
    dur_a = sum(sa[i]["end_ms"] - sa[i]["start_ms"] for i, _ in pairs)
    dur_b = sum(sb[j]["end_ms"] - sb[j]["start_ms"] for _, j in pairs)
    return {"shots": len(pairs), "pairs": pairs, "duration_a_ms": dur_a, "duration_b_ms": dur_b,
            "a": (sa[pairs[0][0]]["start_ms"], sa[pairs[-1][0]]["end_ms"]),
            "b": (sb[pairs[0][1]]["start_ms"], sb[pairs[-1][1]]["end_ms"])}


def audio_run(fa: Mapping[str, Any], fb: Mapping[str, Any], cfg: Mapping[str, Any]) -> Optional[dict[str, Any]]:
    """Najdłuższy wspólny odcinek odcisków audio (dowolne przesunięcie) o średnim BER <= audio_max_ber."""
    import numpy as np
    ba, bb = fa["bits"], fb["bits"]
    w = int(cfg["audio_window_hops"])
    if len(ba) < w or len(bb) < w:
        return None
    sh = np.arange(16)
    A = ((np.asarray(ba)[:, None] >> sh) & 1).astype(np.int8)
    B = ((np.asarray(bb)[:, None] >> sh) & 1).astype(np.int8)
    VA = np.frombuffer(fa["valid"].encode(), dtype=np.uint8) == 49
    VB = np.frombuffer(fb["valid"].encode(), dtype=np.uint8) == 49
    hop = int(fa["hop_ms"])
    best = None
    for o in range(-(len(ba) - w), len(bb) - w + 1):
        i0, i1 = max(0, -o), min(len(ba), len(bb) - o)
        if i1 - i0 < w:
            continue
        mism = (A[i0:i1] != B[i0 + o:i1 + o]).mean(axis=1)
        ok = VA[i0:i1] & VB[i0 + o:i1 + o]
        cs = np.concatenate([[0.0], np.cumsum(np.where(ok, mism, 0.0))])
        cv = np.concatenate([[0], np.cumsum(ok)])
        nv = cv[w:] - cv[:-w]
        ber = (cs[w:] - cs[:-w]) / np.maximum(nv, 1)
        good = (nv >= 0.8 * w) & (ber <= cfg["audio_max_ber"])
        if not good.any():
            continue
        # scal nakładające się dobre okna w ciągłe odcinki
        k = 0
        n = len(good)
        while k < n:
            if not good[k]:
                k += 1
                continue
            e = k
            while e + 1 < n and good[e + 1]:
                e += 1
            length = e - k + w
            sel = slice(k, e + 1)
            mean_ber = float(ber[sel].mean())
            cand = (length, -mean_ber, i0 + k, o)
            if best is None or cand[:2] > best[:2]:
                best = cand
            k = e + 1
    if best is None:
        return None
    length, nber, s, o = best
    return {"a": (s * hop, (s + length) * hop + int(fa["frame_ms"])),
            "b": ((s + o) * hop, (s + o + length) * hop + int(fb["frame_ms"])),
            "hops": length, "ber": round(-nber, 3)}


_WORD = re.compile(r"\w+", re.UNICODE)


def words(segments: Sequence[Mapping[str, Any]]) -> list[tuple[str, int, int]]:
    """(słowo znormalizowane, start_ms, end_ms); czas słów interpolowany liniowo w obrębie segmentu."""
    out = []
    for s in segments:
        ws = _WORD.findall(str(s.get("text", "")).lower())
        if not ws:
            continue
        t0, t1 = int(s["start_ms"]), int(s["end_ms"])
        step = (t1 - t0) / len(ws)
        for k, w in enumerate(ws):
            out.append((w, int(t0 + k * step), int(t0 + (k + 1) * step)))
    return out


def transcript_run(wa: Sequence[tuple[str, int, int]], wb: Sequence[tuple[str, int, int]], cfg: Mapping[str, Any]
                   ) -> Optional[dict[str, Any]]:
    """Najdłuższy wspólny ciąg słów (n-gramy + rozszerzanie na przekątnej); zwraca przedziały i cytat."""
    n = int(cfg["transcript_ngram"])
    if len(wa) < n or len(wb) < n:
        return None
    idx: dict[tuple, list[int]] = {}
    for j in range(len(wb) - n + 1):
        idx.setdefault(tuple(w for w, _, _ in wb[j:j + n]), []).append(j)
    best = None
    for i in range(len(wa) - n + 1):
        for j in idx.get(tuple(w for w, _, _ in wa[i:i + n]), []):
            if i > 0 and j > 0 and wa[i - 1][0] == wb[j - 1][0]:
                continue  # nie początek maksymalnego ciągu
            k = n
            while i + k < len(wa) and j + k < len(wb) and wa[i + k][0] == wb[j + k][0]:
                k += 1
            if best is None or k > best[0]:
                best = (k, i, j)
    if best is None or best[0] < int(cfg["transcript_min_words"]):
        return None
    k, i, j = best
    return {"a": (wa[i][1], wa[i + k - 1][2]), "b": (wb[j][1], wb[j + k - 1][2]), "words": k,
            "quote": " ".join(w for w, _, _ in wa[i:i + k])}


def cosine(u: Sequence[float], v: Sequence[float]) -> float:
    num = sum(a * b for a, b in zip(u, v))
    den = (sum(a * a for a in u) ** 0.5) * (sum(b * b for b in v) ** 0.5)
    return num / den if den else 0.0
