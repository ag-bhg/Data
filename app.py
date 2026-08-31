import os
import re

from flask import Flask, render_template, request, jsonify
from scraper import sync_history
from database import init_db, get_rows, get_rows_by_recency, count_rows, migrate_sort_keys
import sl_engine as engine

app = Flask(__name__)
app.secret_key = "local-demo-only-change-me"

init_db()


def extract_kode_pasaran(periode):
    """
    Kolom "periode" berisi kode pasaran + nomor urut, mis:
    "TTM 22:00-604" -> kode pasaran "TTM 22:00"
    "OR2-606"        -> kode pasaran "OR2"
    Ambil bagian sebelum tanda "-NNN" di paling akhir.
    """
    periode = str(periode or "").strip()

    m = re.match(r"^(.*)-(\d+)$", periode)

    if m:
        return m.group(1).strip()

    return periode


def extract_urutan_pasaran(periode):
    """
    Ambil nomor urut (angka setelah tanda "-" terakhir) dari
    periode, mis "OR2-606" -> 606. Dipakai untuk deteksi gap
    nomor urut per pasaran di /api/nomor-semua.
    """
    periode = str(periode or "").strip()

    m = re.match(r"^(.*)-(\d+)$", periode)

    if m:
        try:
            return int(m.group(2))
        except ValueError:
            return None

    return None


@app.route("/")
def index():
    page = max(1, request.args.get("page", 1, type=int))
    per_page = 25

    rows = get_rows_by_recency(page, per_page)
    total = count_rows()

    pages = max(1, (total + per_page - 1) // per_page)

    return render_template(
        "index.html",
        rows=rows,
        page=page,
        pages=pages,
        total=total
    )


# =========================================================
# AUTO UPDATE — 2 JADWAL BERJALAN BARENGAN
#
# Tombol "Update Data" manual sudah dihapus dari UI. Sekarang ada
# 2 pemicu otomatis yang sama-sama manggil endpoint ini, tapi beda
# tujuan:
#
# 1) GitHub Actions, tiap 5 menit (.github/workflows/auto-update.yml)
#    -> mode default ("5min"), cap max_new_rows=20. Ini tulang
#       punggung update real-time.
#
# 2) Vercel Cron, 1x/hari (vercel.json, path "/api/cron?mode=daily")
#    -> mode "daily", cap hard_cap_pages=6 (~120 data). Cuma
#       jaring pengaman kecil kalau job 5 menit sempat kelewat
#       beberapa siklus (server tidur, deploy, dsb) — BUKAN sapuan
#       besar. Database sudah ~12 ribuan baris, jadi cukup 6
#       halaman/120 data per hari, tidak perlu lebih.
#
# OTORISASI — terima 2 cara sekaligus, karena 2 pemicu di atas
# kirim auth dengan cara beda:
# - GitHub Actions -> header custom "X-Cron-Secret: <CRON_SECRET>"
# - Vercel Cron (bawaan) -> otomatis kirim
#   "Authorization: Bearer <CRON_SECRET>" kalau env var CRON_SECRET
#   di-set di project Vercel-nya (tidak perlu diatur manual).
# Set env var CRON_SECRET yang SAMA di kedua tempat (Vercel env +
# repo secret GitHub) supaya dua-duanya lolos otorisasi.
# =========================================================

@app.get("/api/cron")
def cron_sync():

    expected_secret = os.environ.get("CRON_SECRET", "")
    provided_secret = request.headers.get("X-Cron-Secret", "")

    auth_header = request.headers.get("Authorization", "")
    bearer_secret = auth_header[7:] if auth_header.lower().startswith("bearer ") else ""

    is_authorized = bool(expected_secret) and (
        provided_secret == expected_secret or bearer_secret == expected_secret
    )

    if not is_authorized:
        return jsonify({
            "ok": False,
            "error": "Unauthorized"
        }), 401

    mode = request.args.get("mode", "5min")

    if mode == "daily":
        sync_kwargs = {"max_new_rows": 120, "hard_cap_pages": 6}
    else:
        sync_kwargs = {"max_new_rows": 20}

    try:
        result = sync_history(**sync_kwargs)

        return jsonify({
            "ok": True,
            "mode": mode,
            "message": "Sinkronisasi otomatis berhasil",
            "changed": result["changed"],
            "new_rows": result["new_rows"],
            "pages_scanned": result["pages_scanned"],
            "caught_up": result["caught_up"]
        }), 200

    except Exception as exc:

        return jsonify({
            "ok": False,
            "error": str(exc)
        }), 500


# =========================================================
# UPDATE MANUAL (tombol di index.html)
#
# Beda dengan /api/cron (butuh CRON_SECRET, dipanggil mesin),
# endpoint ini dipanggil dari tombol di halaman utama. Kode
# "13579" cuma pengaman di sisi tampilan (mencegah salah pencet),
# BUKAN autentikasi — siapa pun yang buka halaman ini memang
# sudah bisa lihat /api/nomor dkk tanpa token juga.
#
# Baca 30 halaman (hard_cap_pages=30), max_new_rows dilonggarkan
# supaya tidak berhenti duluan sebelum 30 halaman selesai dibaca
# kalau memang ada banyak data yang belum tertulis. Data yang
# belum ada otomatis ditulis ke database oleh sync_history()
# (lewat upsert_rows).
# =========================================================

@app.post("/api/manual-sync")
def manual_sync():
    kode_input = (request.json or {}).get("kode") if request.is_json else request.form.get("kode")

    if kode_input != "13579":
        return jsonify({"ok": False, "error": "Kode pengaman salah"}), 400

    try:
        result = sync_history(hard_cap_pages=30, max_new_rows=100000)

        return jsonify({
            "ok": True,
            "message": "Update manual selesai",
            "changed": result["changed"],
            "new_rows": result["new_rows"],
            "pages_scanned": result["pages_scanned"],
            "caught_up": result["caught_up"]
        }), 200

    except Exception as exc:
        return jsonify({
            "ok": False,
            "error": str(exc)
        }), 500


# =========================================================
# MIGRASI SATU KALI (isi field sort_key untuk dokumen lama)
#
# Cara pakai:
# 1. Set environment variable MIGRATE_TOKEN di Vercel (string
#    rahasia bebas, misal hasil dari `openssl rand -hex 16`).
# 2. Deploy.
# 3. Buka sekali: https://domain-kamu/api/migrate-sort-key?token=ISI_TOKEN
# 4. Setelah muncul {"ok": true, ...}, migrasi selesai. Endpoint
#    ini aman dipanggil berkali-kali (dokumen yang sudah punya
#    sort_key otomatis dilewati), tapi cukup dijalankan sekali.
# =========================================================

@app.get("/api/migrate-sort-key")
def migrate_sort_key_route():
    token = request.args.get("token", "")
    expected = os.environ.get("MIGRATE_TOKEN", "")

    if not expected or token != expected:
        return jsonify({"ok": False, "error": "Unauthorized"}), 401

    try:
        updated = migrate_sort_keys()

        return jsonify({
            "ok": True,
            "updated": updated
        }), 200

    except Exception as exc:

        return jsonify({
            "ok": False,
            "error": str(exc)
        }), 500


# =========================================================
# SYNC UNTUK indexne.html (Sistem Lama)
#
# Sama fungsinya dengan tombol "Update Data" di sistem baru
# (route /sync di atas), tapi versi JSON + CORS supaya bisa
# dipanggil lewat fetch() dari indexne.html tanpa perlu buka
# situs Vercel-nya sama sekali.
# =========================================================

@app.post("/api/sync")
def api_sync():
    # ?pages=N mengontrol seberapa dalam scraping halaman situs
    # sumber untuk request ini. Dipakai indexne.html untuk
    # menyesuaikan jangkauan baca sesuai lama jeda sejak update
    # terakhir (baca sedikit kalau baru saja update, baca lebih
    # dalam kalau sudah lama absen) — supaya kuota Firestore
    # terjaga.
    pages = request.args.get("pages", 40, type=int)
    pages = max(1, min(pages, 60))

    try:
        result = sync_history(hard_cap_pages=pages)

        resp = jsonify({
            "ok": True,
            "changed": result["changed"],
            "pages_scanned": result["pages_scanned"],
            "caught_up": result["caught_up"]
        })

    except Exception as exc:
        resp = jsonify({
            "ok": False,
            "error": str(exc)
        })

    resp.headers["Access-Control-Allow-Origin"] = "*"

    return resp


# =========================================================
# API DATA UNTUK indexne.html (Analisa Frekuensi)
#
# Dipanggil dari browser (fetch) oleh tombol "Ambil dari Server"
# di indexne.html supaya kotak Data Historis bisa terisi otomatis
# dari data yang sudah tersimpan di Firestore, tanpa copy-paste
# manual. Endpoint ini read-only (tidak butuh token) karena data
# nomor draw memang bukan data rahasia.
#
# PENTING: koleksi "history" berisi CAMPURAN banyak pasaran
# (OR2, TXSE, NJE, VIRN, BLI, HNO, TXSM, TTM 22:00, dst) karena
# scraper.py memang tidak memisahkannya saat menyimpan. Jadi
# /api/nomor WAJIB difilter pakai ?kode=... (lihat
# extract_kode_pasaran) supaya data yang ditarik hanya dari satu
# pasaran, bukan campur semua. Pakai /api/kode-pasaran dulu untuk
# tahu kode apa saja yang tersedia.
#
# ?scan=N menentukan berapa banyak dokumen TERBARU yang dipindai
# untuk dicari kecocokan kode (default 1500, maksimal 3000) —
# supaya tidak membaca seluruh koleksi tiap kali dipanggil.
# ?limit=N membatasi jumlah nomor yang dikembalikan setelah
# difilter (default 500, maksimal 2000).
# =========================================================

@app.get("/api/kode-pasaran")
def api_kode_pasaran():
    # Default dinaikkan dari 1500 -> 2500 supaya tetap menjangkau
    # pasaran yang jarang tampil walau kamu absen beberapa hari.
    scan = request.args.get("scan", 2500, type=int)
    scan = max(1, min(scan, 3000))

    try:
        rows = get_rows(1, scan)

        kode_list = sorted({
            extract_kode_pasaran(r.get("periode"))
            for r in rows
            if r.get("periode")
        })

        resp = jsonify({
            "ok": True,
            "kode": kode_list
        })

    except Exception as exc:
        resp = jsonify({
            "ok": False,
            "error": str(exc)
        })

    resp.headers["Access-Control-Allow-Origin"] = "*"

    return resp


@app.get("/api/nomor")
def api_nomor():
    limit = request.args.get("limit", 500, type=int)
    limit = max(1, min(limit, 2000))

    kode = request.args.get("kode", "").strip()

    # Sama seperti /api/kode-pasaran, dinaikkan biar tetap aman
    # untuk gap beberapa hari.
    scan = request.args.get("scan", 2500, type=int)
    scan = max(1, min(scan, 3000))

    try:
        if kode:
            raw_rows = get_rows(1, scan)
            raw_rows = [
                r for r in raw_rows
                if extract_kode_pasaran(r.get("periode")).lower() == kode.lower()
            ]
            raw_rows = raw_rows[:limit]
        else:
            # Tanpa filter kode, tetap dibatasi limit — tapi INGAT:
            # hasilnya akan campur semua pasaran, hanya cocok
            # dipakai untuk keperluan lain di luar analisis per
            # pasaran (mis. cek total data).
            raw_rows = get_rows(1, limit)

        numbers = [r["nomor"] for r in raw_rows if r.get("nomor")]
        rows = [
            {
                "tanggal": r.get("tanggal"),
                "periode": r.get("periode"),
                "nomor": r.get("nomor"),
            }
            for r in raw_rows if r.get("nomor")
        ]

        resp = jsonify({
            "ok": True,
            "count": len(numbers),
            "kode": kode or None,
            "numbers": numbers,  # dipertahankan untuk kompatibilitas mundur
            "rows": rows         # dipakai SL sekarang: format Tanggal + Periode + Nomor
        })

    except Exception as exc:
        resp = jsonify({
            "ok": False,
            "error": str(exc)
        })

    # Izinkan dipanggil dari indexne.html walau beda domain/hosting.
    resp.headers["Access-Control-Allow-Origin"] = "*"

    return resp


# =========================================================
# SEMUA PASARAN SEKALIGUS (untuk update massal di indexne.html)
#
# Bedanya dengan memanggil /api/nomor satu-satu per pasaran:
# endpoint ini cuma melakukan SATU query Firestore (baca sampai
# `scan` dokumen terbaru), lalu mengelompokkan hasilnya per kode
# pasaran di sisi server. Kalau tombol "Update Data" di
# indexne.html harus menyegarkan 10 pasaran tersimpan, cara lama
# (panggil /api/nomor 10x) berarti 10x baca @scan dokumen —
# boros. Endpoint ini cukup 1x baca untuk semuanya.
# =========================================================

@app.get("/api/nomor-semua")
def api_nomor_semua():
    limit = request.args.get("limit", 500, type=int)
    limit = max(1, min(limit, 5000))

    # ?full=1 dipakai tombol "Update Master" di indexne.html.
    # SENGAJA dibatasi ke MASTER_SCAN_CAP (bukan literal seluruh
    # koleksi) — kalau ikut total koleksi, biayanya akan terus
    # membengkak seiring waktu (koleksi terus bertambah tiap hari).
    # 2000 dokumen masih jauh lebih dari cukup untuk menambal gap
    # yang realistis (~20 hari di kecepatan ~100 data/hari), dan
    # aman dipakai harian kalau perlu (Firestore limit reads 50K/hari).
    full = request.args.get("full", "0") == "1"
    MASTER_SCAN_CAP = 2000

    if full:
        scan = MASTER_SCAN_CAP
    else:
        scan = request.args.get("scan", 700, type=int)
        scan = max(1, min(scan, 3000))

    try:
        rows = get_rows(1, scan)

        grouped = {}
        urutan_sets = {}

        for r in rows:
            periode = r.get("periode")
            kode = extract_kode_pasaran(periode)
            nomor = r.get("nomor")
            urutan = extract_urutan_pasaran(periode)

            if not kode or not nomor:
                continue

            bucket = grouped.setdefault(kode, [])
            if len(bucket) < limit:
                # Objek lengkap (bukan nomor polos) — SL (index.html) sekarang
                # baca field ini lewat matchRows.map(r => ({tanggal, periode, nomor, ...}))
                # di fungsi runServerUpdate.
                bucket.append({
                    "tanggal": r.get("tanggal"),
                    "periode": periode,
                    "nomor": nomor,
                })

            if urutan is not None:
                urutan_sets.setdefault(kode, set()).add(urutan)

        # =================================================
        # DETEKSI GAP NOMOR URUT PER PASARAN
        #
        # Dalam rentang [urutan terkecil, urutan terbesar] yang
        # BENAR-BENAR TERBACA di window scan ini, cari angka yang
        # hilang. Ini valid karena batasnya diambil dari data yang
        # nyata terlihat (bukan asumsi di luar window), jadi tidak
        # akan salah alarm gara-gara window terpotong.
        #
        # CATATAN: window kecil (tier singkat) cuma bisa mendeteksi
        # gap yang kebetulan ada DI DALAM window itu — makin besar
        # scan, makin lengkap gap yang bisa ketahuan.
        # =================================================
        gaps = {}
        for kode, urutan_set in urutan_sets.items():
            if len(urutan_set) < 2:
                gaps[kode] = []
                continue
            lo, hi = min(urutan_set), max(urutan_set)
            gaps[kode] = [n for n in range(lo, hi + 1) if n not in urutan_set]

        resp = jsonify({
            "ok": True,
            "kode_count": len(grouped),
            "scan_used": scan,
            "data": grouped,
            "gaps": gaps
        })

    except Exception as exc:
        resp = jsonify({
            "ok": False,
            "error": str(exc)
        })

    resp.headers["Access-Control-Allow-Origin"] = "*"

    return resp


# =========================================================
# ANALISA (untuk bot Telegram) — port Python dari logika SL,
# lihat sl_engine.py. Jenis yang didukung: js, ai, shio, bbfs, formulax.
# =========================================================

def _get_used_numbers(kode, scan_limit=2500):
    """Ambil nomor (bare, newest-first) 1 pasaran dari Firestore — sama seperti /api/nomor."""
    raw_rows = get_rows(1, scan_limit)
    filtered = [
        r for r in raw_rows
        if extract_kode_pasaran(r.get("periode")).lower() == kode.lower()
    ]
    return [r["nomor"] for r in filtered if r.get("nomor")]


@app.get("/api/analisa/<jenis>")
def api_analisa(jenis):
    kode = request.args.get("kode", "").strip()
    if not kode:
        return jsonify({"ok": False, "error": "Parameter 'kode' wajib diisi."}), 400

    window = request.args.get("window", 30, type=int)
    window = max(5, min(window, 200))
    n = request.args.get("n", 6, type=int)

    try:
        used = _get_used_numbers(kode)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500

    if len(used) < window:
        return jsonify({
            "ok": False,
            "error": f"Data '{kode}' cuma {len(used)}, butuh minimal {window} (window)."
        })

    try:
        if jenis == "js":
            sub = used[:window]
            rows = []
            for num in sub:
                jumlah_list, selisih_list = engine.jumlah_selisih_list(num)
                rows.append({"num": num, "jumlahList": jumlah_list, "selisihList": selisih_list})
            data = {
                "jumlah": engine.best_cover_js_part(rows, "jumlahList", n),
                "selisih": engine.best_cover_js_part(rows, "selisihList", n),
            }

        elif jenis == "ai":
            sub = used[:window]
            data = {k: engine.best_cover_ai_part(sub, k, n) for k in ("AC", "CK", "KE")}

        elif jenis == "shio":
            sub = used[:window]
            data = {"shio": engine.best_cover_shio_gabungan(sub, n)}

        elif jenis == "bbfs":
            sub = used[:window]
            data = {"bbfs": engine.best_cover_bbfs(sub, n)}

        elif jenis == "formulax":
            target_len = len(used[0])
            pos_labels = engine.pos_labels_for_length(target_len)
            recs = engine.compute_formula_x(used, pos_labels, control_n=10, trend_n=30)
            data = {label: arr[:3] for label, arr in recs.items()}

        else:
            return jsonify({"ok": False, "error": f"Jenis '{jenis}' tidak dikenal."}), 400

    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500

    resp = jsonify({
        "ok": True,
        "kode": kode,
        "jenis": jenis,
        "window": window,
        "n": n,
        "count": len(used),
        "data": data,
    })
    resp.headers["Access-Control-Allow-Origin"] = "*"

    return resp


if __name__ == "__main__":
    app.run(
        host="127.0.0.1",
        port=5000,
        debug=True
    )
