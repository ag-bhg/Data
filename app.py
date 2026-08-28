import os
import re

from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from scraper import sync_history
from database import init_db, get_rows, get_rows_by_recency, count_rows, migrate_sort_keys

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


@app.post("/sync")
def sync():
    try:
        result = sync_history()
        changed = result["changed"]
        pages = result["pages_scanned"]

        if result["caught_up"]:
            flash(
                f"Sinkronisasi selesai. {changed} data diproses ({pages} halaman dipindai) — sudah mengejar sampai data terbaru.",
                "success"
            )
        else:
            flash(
                f"Sinkronisasi sebagian: {changed} data diproses ({pages} halaman dipindai), masih ada gap. Klik \"Update Data\" sekali lagi untuk lanjut mengejar sisanya.",
                "success"
            )

    except Exception as exc:
        flash(
            f"Gagal mengambil data: {exc}",
            "error"
        )

    return redirect(url_for("index"))


# =========================================================
# CRON OTOMATIS
# =========================================================

@app.get("/api/cron")
def cron_sync():

    # Vercel Cron mengirim User-Agent ini.
    user_agent = request.headers.get("User-Agent", "")

    if "vercel-cron" not in user_agent.lower():
        return jsonify({
            "ok": False,
            "error": "Unauthorized"
        }), 401

    try:
        result = sync_history()

        return jsonify({
            "ok": True,
            "message": "Sinkronisasi otomatis berhasil",
            "changed": result["changed"],
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

        # BARU: sertakan tanggal + periode (Id+No Urut) per baris, bukan
        # cuma nomor keluaran polos. SL (indexne.html) sebelumnya membaca
        # data 3 kolom "Tanggal - Id+NoUrut - Nomor" (lihat fungsi
        # parseData/quickInputBtn di sana) — kalau cuma dikirim "numbers"
        # saja, SL kehilangan tanggal+periode dan sebagian fiturnya (nomor
        # urut otomatis, deteksi gap) jadi error/salah baca. "numbers"
        # tetap disertakan untuk kompatibilitas lama.
        rows_out = [
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
            "numbers": numbers,
            "rows": rows_out
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
            tanggal = r.get("tanggal")
            urutan = extract_urutan_pasaran(periode)

            if not kode or not nomor:
                continue

            bucket = grouped.setdefault(kode, [])
            if len(bucket) < limit:
                # BARU: simpan objek {tanggal, periode, nomor}, bukan
                # cuma nomor polos, supaya SL bisa menyimpan &
                # menampilkan format "Tgl - Id+NoUrut - Nomor" seperti
                # yang dibaca SL sebelumnya (lihat catatan di /api/nomor
                # di atas untuk alasan lengkapnya).
                bucket.append({
                    "tanggal": tanggal,
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


if __name__ == "__main__":
    app.run(
        host="127.0.0.1",
        port=5000,
        debug=True
    )
