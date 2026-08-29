"""
sl_engine.py — Port Python dari logika analisa SL (index.html).

PENTING: ini duplikat logika, BUKAN sumber tunggal. Kalau rumus di SL (JS)
diubah, fungsi yang sesuai di sini WAJIB diikutkan diubah, supaya hasil bot
tidak "geser" dari yang tampil di SL.

Yang di-port:
- Jumlah & Selisih   (bestCoverJsPart)
- Ai Ai / Angka Ikut (bestCoverAiPart)
- Shi234 / Shio      (bestCoverShioGabungan)
- BBFS               (kriteria "seluruh digit tercakup", beda dari 3 di atas)
- Formula X          (mode default: Kontrol N=10, Jendela Tren N=30, TANPA optimizer
                       Wilson/Walk-Forward/Ensemble — itu belum di-port)
"""
from itertools import combinations

# ============== Kombinasi digit/shio (cache, biar tidak generate ulang tiap panggilan) ==============

_combo_cache = {}
def combos_of_digits(k):
    if k not in _combo_cache:
        _combo_cache[k] = [list(c) for c in combinations(range(10), k)]
    return _combo_cache[k]

_shio_combo_cache = {}
def combos_of_shio(k):
    if k not in _shio_combo_cache:
        _shio_combo_cache[k] = [list(c) for c in combinations(range(1, 13), k)]
    return _shio_combo_cache[k]


def _pick_best(candidates_hits_freq):
    """candidates_hits_freq: iterable of (combo, hits, freq_sum) -> combo terbaik (hits lalu freq_sum, tie-break)."""
    best = None
    for combo, hits, freq_sum in candidates_hits_freq:
        if best is None or hits > best[1] or (hits == best[1] and freq_sum > best[2]):
            best = (combo, hits, freq_sum)
    return sorted(best[0])


# ============== Jumlah & Selisih ==============

def bagian_of_4d(num):
    return {"AC": num[0:2], "CK": num[1:3], "KE": num[2:4]}

def jumlah_selisih_dari_pasangan(pair):
    d = [int(c) for c in pair]
    return (d[0] + d[1]) % 10, abs(d[0] - d[1])

def jumlah_selisih_list(num):
    bagian = bagian_of_4d(num)
    jumlah_list, selisih_list = [], []
    for k in ("AC", "CK", "KE"):
        j, s = jumlah_selisih_dari_pasangan(bagian[k])
        jumlah_list.append(j)
        selisih_list.append(s)
    return jumlah_list, selisih_list

def best_cover_js_part(rows, key, digit_count):
    """rows: list of {"jumlahList"/"selisihList": [int,int,int], ...}"""
    total_freq = [0] * 10
    for r in rows:
        for v in r[key]:
            total_freq[v] += 1

    def gen():
        for combo in combos_of_digits(digit_count):
            hits = sum(1 for r in rows if any(v in combo for v in r[key]))
            freq_sum = sum(total_freq[d] for d in combo)
            yield combo, hits, freq_sum

    return _pick_best(gen())


# ============== Ai Ai (Angka Ikut AC/CK/KE) ==============

def count_digits_ai_part(sub_used, part_key):
    counts = [0] * 10
    for num in sub_used:
        pair = bagian_of_4d(num)[part_key]
        if pair[0] == pair[1]:
            counts[int(pair[0])] += 1  # twin -> 1x
        else:
            counts[int(pair[0])] += 1
            counts[int(pair[1])] += 1
    return counts

def best_cover_ai_part(sub_used, part_key, digit_count):
    pairs = [bagian_of_4d(num)[part_key] for num in sub_used]
    total_freq = count_digits_ai_part(sub_used, part_key)

    def gen():
        for combo in combos_of_digits(digit_count):
            hits = sum(1 for pair in pairs if any(int(ch) in combo for ch in pair))
            freq_sum = sum(total_freq[d] for d in combo)
            yield combo, hits, freq_sum

    return _pick_best(gen())


# ============== Shi234 (Shio gabungan AC+CK+KE) ==============

def shio_of_12(two_digit_str):
    n = int(two_digit_str)
    r = n % 12
    return 12 if r == 0 else r

def shio_of_part(num, part_key):
    return shio_of_12(bagian_of_4d(num)[part_key])

def count_shio_gabungan(sub_used):
    counts = [0] * 13  # index 1-12 dipakai
    for num in sub_used:
        counts[shio_of_part(num, "AC")] += 1
        counts[shio_of_part(num, "CK")] += 1
        counts[shio_of_part(num, "KE")] += 1
    return counts

def best_cover_shio_gabungan(sub_used, pick_count):
    total_freq = count_shio_gabungan(sub_used)
    row_sets = [
        {shio_of_part(num, "AC"), shio_of_part(num, "CK"), shio_of_part(num, "KE")}
        for num in sub_used
    ]

    def gen():
        for combo in combos_of_shio(pick_count):
            hits = sum(1 for s in row_sets if any(sh in s for sh in combo))
            freq_sum = sum(total_freq[d] for d in combo)
            yield combo, hits, freq_sum

    return _pick_best(gen())


# ============== BBFS (kriteria: SELURUH digit angka harus tercakup, beda dari 3 di atas) ==============

def best_cover_bbfs(rows, digit_count):
    total_freq = [0] * 10
    for num in rows:
        for ch in num:
            total_freq[int(ch)] += 1

    def gen():
        for combo in combos_of_digits(digit_count):
            hits = sum(1 for num in rows if all(int(ch) in combo for ch in num))
            freq_sum = sum(total_freq[d] for d in combo)
            yield combo, hits, freq_sum

    return _pick_best(gen())


# ============== Formula X (mode default, tanpa optimizer N) ==============

DIGIT_MAPS = {
    "normal": None,
    "mistikLama": {0: 1, 1: 0, 2: 5, 3: 8, 4: 7, 5: 2, 6: 9, 7: 4, 8: 3, 9: 6},
    "mistikBaru": {0: 8, 1: 7, 2: 6, 3: 9, 4: 5, 5: 4, 6: 2, 7: 1, 8: 0, 9: 3},
    "index":      {0: 5, 1: 6, 2: 7, 3: 8, 4: 9, 5: 0, 6: 1, 7: 2, 8: 3, 9: 4},
}

def apply_digit_map(num_str, dmap):
    if not dmap:
        return num_str
    return "".join(str(dmap[int(ch)]) for ch in num_str)

def mod10(x):
    return (x % 10 + 10) % 10

def naik4turun3(d):
    naik = [mod10(d + i) for i in (1, 2, 3, 4)]
    turun = [mod10(d - i) for i in (1, 2, 3)]
    return [str(x) for x in (naik + [d] + turun)]

def compute_mode_analysis(used, target_len, dmap):
    """used: newest-first. Return ranked_per_pos: list (per posisi) of [{"d","c","pct"}, ...] terurut."""
    mapped = [apply_digit_map(n, dmap) for n in used]
    chrono = list(reversed(mapped))  # terlama -> terbaru

    delta_counts_per_pos = [[0] * 10 for _ in range(target_len)]
    for i in range(len(chrono) - 1):
        for p in range(target_len):
            prev_d = int(chrono[i][p])
            next_d = int(chrono[i + 1][p])
            delta_counts_per_pos[p][mod10(next_d - prev_d)] += 1

    last_digits = [int(mapped[0][p]) for p in range(target_len)]

    ranked_per_pos = []
    for p in range(target_len):
        weights = {}
        for d in range(10):
            needed_delta = mod10(d - last_digits[p])
            weights[d] = delta_counts_per_pos[p][needed_delta]
        total_w = sum(weights.values()) or 1
        ranked = sorted(
            ({"d": d, "c": c, "pct": c / total_w * 100} for d, c in weights.items()),
            key=lambda x: -x["c"],
        )
        ranked_per_pos.append(ranked)
    return ranked_per_pos

def stat_pools_all_sources(window_newest_first, dmap, target_len):
    if len(window_newest_first) < 1:
        return [[] for _ in range(target_len)]
    ranked_per_pos = compute_mode_analysis(window_newest_first, target_len, dmap)
    return [[str(x["d"]) for x in r[:8]] for r in ranked_per_pos]

def bhg_pools_all_sources(window_newest_first, target_len):
    last = [int(ch) for ch in window_newest_first[0]]
    return [naik4turun3(d) for d in last[:target_len]]

def pk_pools_all_sources(window_newest_first, target_len):
    pools = []
    for s_idx in range(target_len):
        anchor = int(window_newest_first[0][s_idx])
        counts = [0] * 10
        for i in range(len(window_newest_first) - 1):
            if any(int(ch) == anchor for ch in window_newest_first[i]):
                next_digit = int(window_newest_first[i + 1][s_idx])
                counts[next_digit] += 1
        ranked = sorted(((str(d), c) for d, c in enumerate(counts)), key=lambda x: -x[1])
        pools.append([d for d, c in ranked[:8]])
    return pools

FX_BHG_PK_MIN = 19
FX_BHG_PK_MAX = 29

FX_BASES = [
    {"code": "NRL", "map": DIGIT_MAPS["normal"], "kind": "stat"},
    {"code": "ML",  "map": DIGIT_MAPS["mistikLama"], "kind": "stat"},
    {"code": "MB",  "map": DIGIT_MAPS["mistikBaru"], "kind": "stat"},
    {"code": "IDX", "map": DIGIT_MAPS["index"], "kind": "stat"},
    {"code": "BHG", "map": None, "kind": "bhg"},
    {"code": "PK",  "map": None, "kind": "pk"},
]

def fx_build_formula_list(pos_labels, control_n):
    target_len = len(pos_labels)
    formulas = []
    for base in FX_BASES:
        for s_idx, source_label in enumerate(pos_labels):
            def make_fn(base=base, s_idx=s_idx):
                def fn(window_newest_first):
                    if base["kind"] == "stat":
                        pool = stat_pools_all_sources(window_newest_first[:control_n], base["map"], target_len)[s_idx]
                        return [pool for _ in pos_labels]
                    if len(window_newest_first) < FX_BHG_PK_MIN:
                        raise ValueError("Data PK/BHG belum cukup (minimal 19).")
                    win = window_newest_first[:FX_BHG_PK_MAX]
                    if base["kind"] == "bhg":
                        pool = bhg_pools_all_sources(win, target_len)[s_idx]
                    else:
                        pool = pk_pools_all_sources(win, target_len)[s_idx]
                    return [pool for _ in pos_labels]
                return fn

            formulas.append({
                "key": f'{base["code"]}_{source_label}',
                "label": base["code"],
                "source": source_label,
                "kind": base["kind"],
                "fn": make_fn(),
            })
    return formulas

def fx_trend_accuracy(formula, chrono_num, control_n, trend_n, pos_idx):
    min_needed = FX_BHG_PK_MIN if formula["kind"] in ("bhg", "pk") else min(control_n, 1)
    hit, total = 0, 0
    i = len(chrono_num) - 1
    while i >= 1 and total < trend_n:
        available = chrono_num[:i]
        if len(available) < min_needed:
            break
        window_newest_first = list(reversed(available))
        try:
            pools = formula["fn"](window_newest_first)
        except Exception:
            break
        target = chrono_num[i]
        if pools[pos_idx] and target[pos_idx] in pools[pos_idx]:
            hit += 1
        total += 1
        i -= 1
    pct = (hit / total * 100) if total > 0 else 0
    return {"hit": hit, "total": total, "pct": pct}

def compute_formula_x(used, pos_labels, control_n=10, trend_n=30):
    """used: newest-first. Return {posLabel: [varian terurut akurasi tertinggi, ...]}"""
    chrono_num = list(reversed(used))
    formulas = fx_build_formula_list(pos_labels, control_n)
    recs = {}
    for idx, label in enumerate(pos_labels):
        arr = []
        for f in formulas:
            r = fx_trend_accuracy(f, chrono_num, control_n, trend_n, idx)
            if r["total"] > 0:
                arr.append({
                    "key": f["key"], "label": f["label"], "source": f["source"],
                    "hit": r["hit"], "total": r["total"], "pct": r["pct"],
                })
        arr.sort(key=lambda x: -x["pct"])
        recs[label] = arr
    return recs

def pos_labels_for_length(target_len):
    return {4: ["A", "C", "K", "E"], 3: ["C", "K", "E"], 2: ["K", "E"]}.get(target_len, ["A", "C", "K", "E"])
