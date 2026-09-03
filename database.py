urn hasil
import os
import re
from datetime import datetime, timedelta, time as dtime

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover — fallback kalau tzdata tak tersedia
    ZoneInfo = None

import psycopg2
import psycopg2.extras
from contextlib import contextmanager


# =========================================================
# DTB2 = POSTGRES (Neon)
#
# Sebelumnya pakai Firestore. Pindah ke Postgres supaya filter +
# urut + hitung total bisa jadi 1 query SQL biasa (indexed WHERE +
# ORDER BY + LIMIT/OFFSET, dan COUNT(*)) tanpa perlu akal-akalan
# tambahan seperti composite index manual di Firestore atau sistem
# cache_periode buat ngirit kuota baca -- di Postgres tidak ada
# konsep "kuota per dokumen dibaca", jadi keduanya sudah tidak
# relevan lagi dan dihapus dari sini.
#
# Fungsi & signature di file ini SENGAJA dipertahankan sama persis
# dengan versi Firestore sebelumnya (nama, urutan argumen, bentuk
# hasil), supaya app.py dan scraper.py tidak perlu diubah -- mereka
# cuma memanggil nama fungsi ini, tidak pernah pegang objek
# database secara langsung.
# =========================================================

def get_db_connection():
    """
    Bikin koneksi baru ke Postgres tiap dipanggil -- cocok untuk
    pola serverless (tiap invocation function pendek, tidak ada
    proses long-running yang pegang 1 koneksi terus-menerus).

    PENTING SAAT SETUP: isi env var DATABASE_URL dengan connection
    string versi "Pooled connection" dari dashboard Neon (ada
    "-pooler" di nama hostnya), BUKAN "Direct connection" --
    supaya banyak invocation serverless yang jalan bersamaan tidak
    kehabisan slot koneksi di sisi Postgres.

    Sekalian set 2 timeout jaring-pengaman di level SESSION koneksi
    ini (bukan ubah setting global Neon):
    - statement_timeout: 1 query yang somehow lambat (mis. Neon
      lagi cold start) dipotong paksa, tidak menahan koneksi lama.
    - idle_in_transaction_session_timeout: kalau ada transaksi yang
      lupa di-commit/rollback (mis. karena bug), Postgres yang
      otomatis motong -- mencegah koneksi "nyangkut" dan
      menghabiskan slot pooler.
    """
    dsn = os.environ.get("DATABASE_URL")

    if not dsn:
        raise RuntimeError("DATABASE_URL belum diset")

    conn = psycopg2.connect(
        dsn,
        sslmode="require",
        cursor_factory=psycopg2.extras.RealDictCursor,
    )
    with conn.cursor() as cur:
        cur.execute("SET statement_timeout = '10s';")
        cur.execute("SET idle_in_transaction_session_timeout = '30s';")
    conn.commit()
    return conn


@contextmanager
def _borrow_connection(conn=None):
    """
    Dipakai SEMUA fungsi baca (get_rows, count_rows, dst) supaya
    bisa jalan dengan 2 cara sekaligus tanpa 2 versi kode:

    1) conn=None (perilaku LAMA, dipakai scraper.py/migrasi/dsb
       yang jalan DI LUAR 1 siklus request Flask): bikin koneksi
       baru sendiri di sini, tutup sendiri juga di sini setelah
       selesai. Tidak ada yang berubah untuk pemanggil lama.

    2) conn diisi (dipakai app.py lewat Flask `g`, 1 koneksi dipakai
       bareng untuk beberapa query dalam 1 request "/"): pakai
       persis koneksi yang dikasih, TIDAK ditutup di sini -- siklus
       hidupnya milik pemanggil (app.py yang nutup lewat
       teardown_request).

    Dua-duanya: kalau ada error di tengah query, rollback dulu
    sebelum error itu dilempar lagi -- supaya transaksi tidak
    nyangkut di status "idle in transaction", baik untuk koneksi
    sendiri maupun koneksi pinjaman.
    """
    owns_connection = conn is None
    if owns_connection:
        conn = get_db_connection()
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    finally:
        if owns_connection:
            conn.close()


def extract_kode_pasaran(periode):
    """
    Kolom "periode" berisi kode pasaran + nomor urut, mis:
    "TTM 22:00-604" -> kode pasaran "TTM 22:00"
    "OR2-606"        -> kode pasaran "OR2"
    Ambil bagian sebelum tanda "-NNN" di paling akhir.

    (Fungsi murni, tidak tersentuh migrasi database -- tetap sama
    persis dengan versi Firestore, dipakai app.py & scraper.py.)
    """
    periode = str(periode or "").strip()

    m = re.match(r"^(.*)-(\d+)$", periode)

    if m:
        return m.group(1).strip()

    return periode


def extract_urutan_pasaran(periode):
    """
    Ambil nomor urut (angka setelah tanda "-" terakhir) dari
    periode, mis "OR2-606" -> 606. None kalau formatnya tidak
    cocok pola ini.
    """
    periode = str(periode or "").strip()

    m = re.match(r"^(.*)-(\d+)$", periode)

    if m:
        try:
            return int(m.group(2))
        except ValueError:
            return None

    return None


def compute_group_sort_key(tanggal, urutan):
    """
    Sort key KHUSUS PER-PASARAN, dipakai bandingkan progres SATU
    kode pasaran dengan progres pasaran itu SENDIRI di sync
    sebelumnya -- bukan dibandingkan silang dengan kode pasaran
    lain. Tidak berubah dari versi Firestore.
    """
    tanggal = str(tanggal or "").strip()

    try:
        day, month, year = tanggal.split("-")
        tanggal_key = f"{year}-{month}-{day}"
    except ValueError:
        tanggal_key = "0000-00-00"

    try:
        urutan_key = f"{int(urutan):010d}"
    except (TypeError, ValueError):
        urutan_key = "0" * 10

    return f"{tanggal_key}_{urutan_key}"


def compute_sort_key(tanggal, periode):
    """
    Bikin string yang bisa diurutkan langsung (ORDER BY), karena
    "tanggal" disimpan dalam format dd-mm-yyyy yang urutan aslinya
    tidak kronologis. Tidak berubah dari versi Firestore.
    """
    tanggal = str(tanggal or "").strip()
    periode = str(periode or "").strip()

    try:
        day, month, year = tanggal.split("-")
        tanggal_key = f"{year}-{month}-{day}"
    except ValueError:
        tanggal_key = "0000-00-00"

    periode_key = periode.zfill(10) if periode.isdigit() else periode

    return f"{tanggal_key}_{periode_key}"


def init_db():
    """
    Buat tabel & index kalau belum ada. Aman dipanggil berkali-kali
    (CREATE TABLE/INDEX IF NOT EXISTS) -- dipanggil sekali tiap
    cold start dari app.py, sama seperti sebelumnya.
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS history (
                    id BIGSERIAL PRIMARY KEY,
                    tanggal TEXT NOT NULL,
                    periode TEXT NOT NULL,
                    nomor TEXT NOT NULL,
                    source_url TEXT,
                    sort_key TEXT NOT NULL,
                    kode_pasaran TEXT NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    UNIQUE (tanggal, periode, nomor)
                );
            """)
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_history_sort_key "
                "ON history (sort_key DESC);"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_history_kode_sort "
                "ON history (kode_pasaran, sort_key DESC);"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_history_updated_at "
                "ON history (updated_at DESC);"
            )
            cur.execute("""
                CREATE TABLE IF NOT EXISTS sync_state (
                    kode_pasaran TEXT PRIMARY KEY,
                    sort_key TEXT NOT NULL
                );
            """)
            # Cache hasil "digit terbanyak" per (tanggal, opsi, zona)
            # untuk fitur Analisis Angka per Zona Waktu -- lihat
            # analisis_waktu.py. urutan_digit NULL berarti "sudah
            # dihitung tapi belum cukup pasaran keluar" (bukan
            # "belum pernah dihitung"), supaya tidak dihitung ulang
            # sia-sia. Baris untuk tanggal HARI INI sengaja tidak
            # pernah ditulis ke sini (datanya masih bisa berubah).
            cur.execute("""
                CREATE TABLE IF NOT EXISTS analisis_zona_cache (
                    tanggal TEXT NOT NULL,
                    opsi SMALLINT NOT NULL,
                    zona_label TEXT NOT NULL,
                    urutan_digit TEXT,
                    PRIMARY KEY (tanggal, opsi, zona_label)
                );
            """)
        conn.commit()
    finally:
        conn.close()


def get_pasaran_progress(conn=None):
    """
    Ambil progres TERAKHIR per kode pasaran -- {kode: sort_key}.
    Sama perannya dengan versi Firestore, cuma sumbernya sekarang
    tabel "sync_state" di Postgres.

    `conn`: opsional, lihat docstring _borrow_connection().
    """
    with _borrow_connection(conn) as c:
        with c.cursor() as cur:
            cur.execute("SELECT kode_pasaran, sort_key FROM sync_state;")
            rows = cur.fetchall()
        return {r["kode_pasaran"]: r["sort_key"] for r in rows}


def update_pasaran_progress(rows, known_progress):
    """
    Majukan progres per-pasaran berdasarkan baris yang BENAR-BENAR
    terbaca di batch ini. Progres HANYA PERNAH NAIK -- kode yang
    tidak berubah tidak ditulis ulang. Logika perbandingan sama
    persis dengan versi Firestore, cuma penyimpanannya lewat
    INSERT ... ON CONFLICT DO UPDATE (setara "merge=True").
    """
    best_per_kode = {}

    for row in rows:
        urutan = extract_urutan_pasaran(row.get("periode"))
        if urutan is None:
            continue

        kode = extract_kode_pasaran(row.get("periode"))
        group_key = compute_group_sort_key(row.get("tanggal"), urutan)

        current_best = best_per_kode.get(kode)
        if current_best is None or group_key > current_best:
            best_per_kode[kode] = group_key

    to_write = {
        kode: group_key
        for kode, group_key in best_per_kode.items()
        if group_key > (known_progress.get(kode) or "")
    }

    if not to_write:
        return 0

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            for kode, group_key in to_write.items():
                cur.execute(
                    """
                    INSERT INTO sync_state (kode_pasaran, sort_key)
                    VALUES (%s, %s)
                    ON CONFLICT (kode_pasaran)
                    DO UPDATE SET sort_key = EXCLUDED.sort_key;
                    """,
                    (kode, group_key),
                )
        conn.commit()
    finally:
        conn.close()

    return len(to_write)


def set_sync_state_raw(kode, sort_key):
    """
    Set 1 baris sync_state LANGSUNG, tanpa lewat perbandingan
    "cuma boleh naik" di update_pasaran_progress().

    DIPAKAI KHUSUS oleh skrip migrasi satu-kali dari Firestore
    (lihat migrate_legacy.py) untuk memindahkan progres lama yang
    sudah tervalidasi apa adanya -- bukan dipakai alur sync
    reguler.
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO sync_state (kode_pasaran, sort_key)
                VALUES (%s, %s)
                ON CONFLICT (kode_pasaran)
                DO UPDATE SET sort_key = EXCLUDED.sort_key;
                """,
                (kode, sort_key),
            )
        conn.commit()
    finally:
        conn.close()


MAX_ROWS_PER_PERIODE = 120


def upsert_rows(rows):
    """
    Simpan baris baru. Dedup dalam batch berdasarkan
    tanggal+periode+nomor sama seperti sebelumnya, lalu ditulis
    lewat INSERT ... ON CONFLICT DO UPDATE (constraint UNIQUE di
    init_db() berperan sama seperti doc_id deterministik di
    Firestore dulu -- baris yang sama persis aman ditulis ulang,
    tidak akan dobel).

    PEMBATAS DATA PER PERIODE: setelah baris baru ditulis, tiap
    kode pasaran yang KENA baris baru di batch ini langsung
    dipangkas supaya tidak lebih dari MAX_ROWS_PER_PERIODE (120)
    baris -- yang dibuang adalah data PALING LAMA (sort_key
    terkecil), model antrian FIFO: data baru selalu bisa masuk,
    yang paling lama otomatis tergeser keluar duluan.

    Cuma dijalankan untuk kode yang benar-benar dapat baris baru
    di batch ini (bukan scan ke-95 kode tiap kali sync) -- kode
    lain yang kebetulan sudah lebih dari 120 (mis. sisa migrasi
    dari Firestore) baru ikut kepangkas begitu kode itu dapat
    baris baru berikutnya, bukan langsung sekaligus semua saat
    deploy ini.
    """

    unique_rows = {}

    for row in rows:
        tanggal = str(row["tanggal"]).strip()
        periode = str(row["periode"]).strip()
        nomor = str(row["nomor"]).strip()

        key = (tanggal, periode, nomor)

        if key not in unique_rows:
            unique_rows[key] = row

    rows = list(unique_rows.values())

    if not rows:
        return 0

    conn = get_db_connection()
    changed = 0
    kode_terpengaruh = set()

    try:
        with conn.cursor() as cur:
            for row in rows:
                tanggal = str(row["tanggal"]).strip()
                periode = str(row["periode"]).strip()
                nomor = str(row["nomor"]).strip()

                sort_key = compute_sort_key(tanggal, periode)
                kode = extract_kode_pasaran(periode)
                kode_terpengaruh.add(kode)

                cur.execute(
                    """
                    INSERT INTO history
                        (tanggal, periode, nomor, source_url,
                         sort_key, kode_pasaran, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, now())
                    ON CONFLICT (tanggal, periode, nomor) DO UPDATE SET
                        source_url = EXCLUDED.source_url,
                        sort_key = EXCLUDED.sort_key,
                        kode_pasaran = EXCLUDED.kode_pasaran,
                        updated_at = now();
                    """,
                    (tanggal, periode, nomor, row.get("source_url"),
                     sort_key, kode),
                )
                changed += 1

            for kode in kode_terpengaruh:
                cur.execute(
                    """
                    DELETE FROM history
                    WHERE kode_pasaran = %s
                      AND id NOT IN (
                          SELECT id FROM history
                          WHERE kode_pasaran = %s
                          ORDER BY sort_key DESC
                          LIMIT %s
                      );
                    """,
                    (kode, kode, MAX_ROWS_PER_PERIODE),
                )
        conn.commit()
    finally:
        conn.close()

    return changed


def get_rows(page, per_page, kode=None, conn=None):
    """
    Ambil satu halaman baris, diurutkan sort_key DESC (tanggal
    draw + periode -- dipakai fitur backend yang butuh urutan
    kronologis draw). Filter opsional per kode pasaran, dipakai
    dropdown periode di "/".

    LIMIT/OFFSET native Postgres -- tidak ada lagi drama composite
    index seperti Firestore, cukup index (kode_pasaran, sort_key)
    yang sudah dibuat di init_db().

    `conn`: opsional, lihat docstring _borrow_connection().
    """

    offset = (page - 1) * per_page

    with _borrow_connection(conn) as c:
        with c.cursor() as cur:
            if kode:
                cur.execute(
                    """
                    SELECT tanggal, periode, nomor, source_url
                    FROM history
                    WHERE kode_pasaran = %s
                    ORDER BY sort_key DESC
                    LIMIT %s OFFSET %s;
                    """,
                    (kode, per_page, offset),
                )
            else:
                cur.execute(
                    """
                    SELECT tanggal, periode, nomor, source_url
                    FROM history
                    ORDER BY sort_key DESC
                    LIMIT %s OFFSET %s;
                    """,
                    (per_page, offset),
                )
            rows = cur.fetchall()
        return [dict(r) for r in rows]


def get_rows_as_of(kode, per_page, tanggal_acuan=None, conn=None):
    """
    Ambil <per_page> baris TERBARU untuk 1 kode pasaran, dihitung
    mundur dari tanggal_acuan (format "YYYY-MM-DD", cocok langsung
    dengan nilai <input type="date">) -- bukan selalu dari data
    paling baru/hari ini.

    tanggal_acuan=None (atau string kosong) berarti "sampai
    sekarang" -- balik ke perilaku get_rows() biasa.

    Dipakai analisis_waktu.py: dulu tabel 30-draw-terakhir selalu
    dihitung mundur dari data terbaru (== hari ini), sekarang bisa
    dihitung mundur dari tanggal manapun yang dipilih user.

    Perbandingan pakai LEFT(sort_key, 10) -- 10 karakter pertama
    sort_key selalu "YYYY-MM-DD" (lihat compute_sort_key), jadi
    ini murni bandingkan tanggal, tidak kena isu perbandingan
    string pada bagian kode-periode setelahnya.

    `conn`: opsional, lihat docstring _borrow_connection().
    """
    if not tanggal_acuan:
        return get_rows(1, per_page, kode=kode, conn=conn)

    with _borrow_connection(conn) as c:
        with c.cursor() as cur:
            cur.execute(
                """
                SELECT tanggal, periode, nomor, source_url
                FROM history
                WHERE kode_pasaran = %s AND LEFT(sort_key, 10) <= %s
                ORDER BY sort_key DESC
                LIMIT %s;
                """,
                (kode, tanggal_acuan, per_page),
            )
            rows = cur.fetchall()
        return [dict(r) for r in rows]


def get_rows_by_recency(page, per_page, conn=None):
    """
    Sama seperti get_rows(), tapi diurutkan berdasarkan
    updated_at (kapan baris terakhir ditulis/disentuh), bukan
    sort_key. Dipertahankan untuk kompatibilitas -- tidak dipanggil
    di alur utama saat ini.

    `conn`: opsional, lihat docstring _borrow_connection().
    """

    offset = (page - 1) * per_page

    with _borrow_connection(conn) as c:
        with c.cursor() as cur:
            cur.execute(
                """
                SELECT tanggal, periode, nomor, source_url
                FROM history
                ORDER BY updated_at DESC
                LIMIT %s OFFSET %s;
                """,
                (per_page, offset),
            )
            rows = cur.fetchall()
        return [dict(r) for r in rows]


def count_rows(kode=None, conn=None):
    """
    Hitung total baris (opsional per kode pasaran). COUNT(*) biasa
    -- tidak ada lagi konsep "kuota per 1000 entri index" seperti
    aggregation query Firestore, dan untuk ukuran data proyek ini
    (belasan ribu baris) tetap cepat tanpa perlu index tambahan.

    `conn`: opsional, lihat docstring _borrow_connection().
    """

    with _borrow_connection(conn) as c:
        with c.cursor() as cur:
            if kode:
                cur.execute(
                    "SELECT COUNT(*) AS total FROM history WHERE kode_pasaran = %s;",
                    (kode,),
                )
            else:
                cur.execute("SELECT COUNT(*) AS total FROM history;")
            row = cur.fetchone()
        return row["total"]


def get_kode_pasaran_options(conn=None):
    """
    Daftar semua kode pasaran yang tersedia, untuk isi dropdown
    periode di "/". Diambil dari tabel "sync_state" (kecil, 1 baris
    per kode pasaran), bukan scan tabel "history" yang besar.

    `conn`: opsional, lihat docstring _borrow_connection().
    """

    with _borrow_connection(conn) as c:
        with c.cursor() as cur:
            cur.execute("SELECT kode_pasaran FROM sync_state ORDER BY kode_pasaran;")
            rows = cur.fetchall()
        return [r["kode_pasaran"] for r in rows]


def get_max_sort_key(conn=None):
    """
    Ambil sort_key TERBESAR (data terbaru) yang sudah tersimpan.
    Dipertahankan untuk kompatibilitas -- tidak dipanggil di alur
    utama saat ini (scraper.py pakai get_pasaran_progress()).

    `conn`: opsional, lihat docstring _borrow_connection().
    """

    with _borrow_connection(conn) as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT sort_key FROM history ORDER BY sort_key DESC LIMIT 1;"
            )
            row = cur.fetchone()
        return row["sort_key"] if row else None


# =========================================================
# JADWAL JAM TUTUP & JAM HASIL PER PASARAN
#
# Sumber: daftar jam yang diberikan user (dari halaman "Jadwal
# Operasional" tiap m=ID di situs sumber), dicocokkan manual ke
# kode pasaran (hasil extract_kode_pasaran) satu-satu dan sudah
# dikonfirmasi user. Data statis -- disimpan sebagai dict Python
# di sini, BUKAN tabel database, karena jarang berubah.
#
# "hari": None berarti "Setiap hari". Kalau diisi (frozenset nama
# hari Indonesia), pasaran itu LIBUR di luar hari yang disebut.
#
# "draws": list (jam_tutup, jam_hasil) -- hampir semua cuma 1
# pasang, KECUALI KK (KINGKONG) yang jalan 2x sehari.
#
# Semua jam dalam WIB.
# =========================================================

JADWAL_PASARAN = {
    "TTM 13:00": {"nama": "TOTO MACAU 13:00", "draws": [("13:00", "13:15")], "hari": None},
    "TTM 00:00": {"nama": "TOTO MACAU 00:00", "draws": [("00:00", "00:15")], "hari": None},
    "TTM 16:00": {"nama": "TOTO MACAU 16:00", "draws": [("16:00", "16:15")], "hari": None},
    "TTM 19:00": {"nama": "TOTO MACAU 19:00", "draws": [("19:00", "19:15")], "hari": None},
    "TTM 22:00": {"nama": "TOTO MACAU 22:00", "draws": [("22:00", "22:15")], "hari": None},
    "TTM 23:00": {"nama": "TOTO MACAU 23:00", "draws": [("23:00", "23:15")], "hari": None},
    "HK":   {"nama": "HONGKONG",       "draws": [("22:45", "23:00")], "hari": None},
    "HKE":  {"nama": "HONGKONGEVE",    "draws": [("18:15", "18:30")], "hari": None},
    "HKL":  {"nama": "HONGKONG LOTTO", "draws": [("22:45", "23:00")], "hari": None},
    "SYD":  {"nama": "SYDNEY",         "draws": [("13:30", "13:50")], "hari": None},
    "SDYL": {"nama": "SYDNEY LOTTO",   "draws": [("13:30", "13:50")], "hari": None},
    "SG":   {"nama": "SINGAPORE",      "draws": [("17:30", "17:45")],
             "hari": frozenset({"Senin", "Rabu", "Kamis", "Sabtu", "Minggu"})},
    "OR1":  {"nama": "OREGON 1", "draws": [("02:45", "03:00")], "hari": None},
    "OR2":  {"nama": "OREGON 2", "draws": [("05:45", "06:00")], "hari": None},
    "OR3":  {"nama": "OREGON 3", "draws": [("08:45", "09:00")], "hari": None},
    "OR4":  {"nama": "OREGON 4", "draws": [("11:45", "12:05")], "hari": None},
    "GGM":  {"nama": "GEORGIA MID", "draws": [("23:15", "23:30")], "hari": None},
    "GEOE": {"nama": "GEORGIA EVE", "draws": [("05:45", "06:00")], "hari": None},
    "GEON": {"nama": "GEORGIA NGT", "draws": [("10:20", "10:35")], "hari": None},
    "MRM":  {"nama": "MARYLAND MID", "draws": [("23:15", "23:30")], "hari": None},
    "MLE":  {"nama": "MARYLAND EVE", "draws": [("06:40", "06:55")], "hari": None},
    "OHM":  {"nama": "OHIO MID", "draws": [("23:15", "23:30")], "hari": None},
    "OHIE": {"nama": "OHIO EVE", "draws": [("06:15", "06:30")], "hari": None},
    "ORL":  {"nama": "ORLANDO", "draws": [("00:30", "00:40")], "hari": None},
    "NJM":  {"nama": "NEW JERSEY MID", "draws": [("23:45", "00:00")], "hari": None},
    "NJE":  {"nama": "NEW JERSEY EVE", "draws": [("09:45", "10:00")], "hari": None},
    "MICM": {"nama": "MICHIGAN MID", "draws": [("23:45", "00:00")], "hari": None},
    "MICE": {"nama": "MICHIGAN EVE", "draws": [("06:15", "06:30")], "hari": None},
    "TRK":  {"nama": "TURKI", "draws": [("01:10", "01:25")], "hari": None},
    "INDM": {"nama": "INDIANA MID", "draws": [("00:05", "00:20")], "hari": None},
    "INDE": {"nama": "INDIANA EVE", "draws": [("09:35", "10:05")], "hari": None},
    "KTM":  {"nama": "KENTUCKY MID", "draws": [("00:05", "00:20")], "hari": None},
    "KTE":  {"nama": "KENTUCKY EVE", "draws": [("09:45", "10:00")], "hari": None},
    "TENM": {"nama": "TENNESSE MID", "draws": [("00:05", "00:20")],
              "hari": frozenset({"Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"})},
    "TENE": {"nama": "TENNESSE EVE", "draws": [("06:00", "06:20")], "hari": None},
    "TENMD": {"nama": "TENNESSE MOR", "draws": [("21:05", "21:20")],
              "hari": frozenset({"Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu"})},
    "BLRS": {"nama": "BELARUS", "draws": [("01:20", "01:30")], "hari": None},
    "TXD":  {"nama": "TEXAS DAY", "draws": [("00:15", "00:30")],
             "hari": frozenset({"Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"})},
    "TXSE": {"nama": "TEXAS EVE", "draws": [("05:45", "06:00")],
             "hari": frozenset({"Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"})},
    "TXSN": {"nama": "TEXAS NGT", "draws": [("09:55", "10:05")],
             "hari": frozenset({"Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"})},
    "TXSM": {"nama": "TEXAS MOR", "draws": [("21:45", "22:00")],
             "hari": frozenset({"Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu"})},
    "FLRM": {"nama": "FLORIDA MID", "draws": [("00:15", "00:30")], "hari": None},
    "FLRE": {"nama": "FLORIDA EVE", "draws": [("08:30", "08:45")], "hari": None},
    "ILM":  {"nama": "ILLINOIS MID", "draws": [("00:25", "00:40")], "hari": None},
    "ILE":  {"nama": "ILLINOIS EVE", "draws": [("09:05", "09:40")], "hari": None},
    "MISM": {"nama": "MISSOURI MID", "draws": [("00:30", "00:45")], "hari": None},
    "MISE": {"nama": "MISSOURI EVE", "draws": [("08:40", "09:00")], "hari": None},
    "DELD": {"nama": "DELAWARE DAY", "draws": [("00:40", "01:00")], "hari": None},
    "DLWN": {"nama": "DELAWARE NGT", "draws": [("06:40", "06:55")], "hari": None},
    "VIRD": {"nama": "VIRGINIA DAY", "draws": [("00:40", "01:00")], "hari": None},
    "VIRN": {"nama": "VIRGINIA NGT", "draws": [("09:40", "10:00")], "hari": None},
    "WDM":  {"nama": "WASHINGTON MID", "draws": [("00:40", "01:00")], "hari": None},
    "WDE":  {"nama": "WASHINGTON EVE", "draws": [("06:40", "06:55")], "hari": None},
    "RM":   {"nama": "ROMA", "draws": [("02:00", "02:10")], "hari": None},
    "NYM":  {"nama": "NEWYORK MID", "draws": [("01:10", "01:30")], "hari": None},
    "NYE":  {"nama": "NEW YORK EVE", "draws": [("09:10", "09:30")], "hari": None},
    "HELS": {"nama": "HELSINKI", "draws": [("02:25", "02:35")], "hari": None},
    "CRD":  {"nama": "CAROLINE DAY", "draws": [("01:45", "02:00")], "hari": None},
    "CRE":  {"nama": "CAROLINE EVE", "draws": [("10:05", "10:20")], "hari": None},
    "PNM":  {"nama": "PANAMA", "draws": [("03:00", "03:10")], "hari": None},
    "YSLM": {"nama": "YERUSALEM", "draws": [("03:20", "03:30")], "hari": None},
    "POL":  {"nama": "POLANDIA", "draws": [("03:40", "03:50")], "hari": None},
    "NWC":  {"nama": "NEWCASTLE", "draws": [("05:00", "05:10")], "hari": None},
    "DET":  {"nama": "DETROIT", "draws": [("05:50", "06:00")], "hari": None},
    "HWI":  {"nama": "HAWAII", "draws": [("06:00", "06:10")], "hari": None},
    "GDC":  {"nama": "GOLDCOAST", "draws": [("06:30", "06:40")], "hari": None},
    "TKY":  {"nama": "TOKYO", "draws": [("07:30", "07:40")], "hari": None},
    "PP":   {"nama": "PAPUA", "draws": [("08:10", "08:20")], "hari": None},
    "MXC":  {"nama": "MEXICO", "draws": [("09:15", "09:25")], "hari": None},
    "SZ":   {"nama": "SHENZHEN", "draws": [("09:40", "09:50")], "hari": None},
    "SHG":  {"nama": "SHANGHAI", "draws": [("10:00", "10:10")], "hari": None},
    "TW":   {"nama": "TAIWAN", "draws": [("20:30", "20:45")], "hari": None},
    "TWM":  {"nama": "TAIWANMOR", "draws": [("10:15", "10:30")], "hari": None},
    "HCM":  {"nama": "HOCHIMINH", "draws": [("11:00", "11:10")], "hari": None},
    "MNL":  {"nama": "MANILA", "draws": [("11:15", "11:30")], "hari": None},
    "BSN":  {"nama": "BUSAN", "draws": [("12:00", "12:10")], "hari": None},
    "VTM":  {"nama": "VIETNAM", "draws": [("12:15", "12:25")], "hari": None},
    "MND":  {"nama": "MANADO", "draws": [("13:00", "13:10")], "hari": None},
    "BLI":  {"nama": "BOLAI", "draws": [("14:10", "14:20")], "hari": None},
    "HNO":  {"nama": "HANOI", "draws": [("14:25", "14:35")], "hari": None},
    "PH":   {"nama": "PHILIPHINE", "draws": [("15:05", "15:15")], "hari": None},
    "CHN":  {"nama": "CHINA", "draws": [("15:15", "15:30")], "hari": None},
    "BJI":  {"nama": "BEIJING", "draws": [("16:10", "16:20")], "hari": None},
    "KR":   {"nama": "KOREA", "draws": [("16:30", "16:40")], "hari": None},
    "KK":   {"nama": "KINGKONG", "draws": [("17:00", "17:10"), ("23:30", "23:40")], "hari": None},
    "JP":   {"nama": "JAPAN", "draws": [("17:00", "17:20")], "hari": None},
    "DXB":  {"nama": "DUBAI", "draws": [("00:15", "00:25")], "hari": None},
    "KBJ":  {"nama": "KAMBOJA", "draws": [("19:00", "19:15")], "hari": None},
    "JJ":   {"nama": "JEJU", "draws": [("19:30", "19:40")], "hari": None},
    "PEN":  {"nama": "PENANG", "draws": [("20:00", "20:10")], "hari": None},
    "PCSO": {"nama": "PCSO", "draws": [("19:45", "20:15")],
             "hari": frozenset({"Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu"})},
    "LDN":  {"nama": "LONDON", "draws": [("21:00", "21:10")], "hari": None},
    "BLG":  {"nama": "BULGARIA", "draws": [("23:30", "23:40")], "hari": None},
    "CD":   {"nama": "CAMBODIA", "draws": [("11:35", "11:50")], "hari": None},
    "BUE":  {"nama": "BULLSEYE", "draws": [("12:50", "13:10")], "hari": None},
}

WIB = ZoneInfo("Asia/Jakarta") if ZoneInfo else None

HARI_INDO = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]

TOLERANSI_TELAT_MENIT = 15


def get_jadwal(kode):
    """
    Info jadwal 1 kode pasaran (nama, jam tutup/hasil per draw,
    hari aktif) -- dipakai buat panel info saat 1 periode difilter
    di "/". None kalau kode-nya belum ada di JADWAL_PASARAN (mis.
    pasaran baru yang belum sempat ditambahkan manual).
    """
    return JADWAL_PASARAN.get(kode)


def get_kode_pasaran_status(conn=None):
    """
    Status "sudah update / telat" untuk tiap kode pasaran yang
    ADA JADWALNYA, dibandingkan jam hasil di JADWAL_PASARAN vs
    data terakhir tersimpan (sync_state, lewat get_pasaran_progress).

    `conn`: opsional, lihat docstring _borrow_connection() --
    diteruskan ke get_pasaran_progress() di bawah.

    Return: {kode: {"nama", "tutup", "hasil", "telat", "terakhir"}}
    - "tutup"/"hasil": gabungan semua draw hari ini, dipisah koma
      (biasanya cuma 1, KECUALI KK yang 2x sehari)
    - "telat": True (sudah lewat jam + toleransi tapi belum ada
      data hari ini), False (sudah update hari ini), atau None
      (belum waktunya / pasaran libur hari ini -- BUKAN status
      buruk, cuma belum bisa dinilai)
    - "terakhir": tanggal draw terakhir tersimpan, format
      "DD-MM-YYYY", atau None kalau belum pernah ada data sama
      sekali

    CATATAN: perbandingan disederhanakan per-hari-kalender WIB,
    tidak menangani kasus lintas tengah malam yang presisi (draw
    jam 00:xx dianggap "milik" hari kalender itu juga). Cukup
    untuk indikator "kelihatannya telat" di tampilan, bukan alat
    otomasi presisi tinggi.
    """
    if WIB is None:
        return {}

    now = datetime.now(WIB)
    today = now.date()
    weekday_name = HARI_INDO[now.weekday()]

    progress = get_pasaran_progress(conn=conn)

    status = {}

    for kode, info in JADWAL_PASARAN.items():
        aktif_hari_ini = info["hari"] is None or weekday_name in info["hari"]

        sort_key = progress.get(kode)
        terakhir_date = None
        if sort_key:
            try:
                terakhir_date = datetime.strptime(
                    sort_key.split("_")[0], "%Y-%m-%d"
                ).date()
            except ValueError:
                terakhir_date = None

        tutup_str = ", ".join(d[0] for d in info["draws"])
        hasil_str_gabungan = ", ".join(d[1] for d in info["draws"])

        if not aktif_hari_ini:
            status[kode] = {
                "nama": info["nama"],
                "tutup": tutup_str,
                "hasil": hasil_str_gabungan,
                "telat": None,
                "terakhir": terakhir_date.strftime("%d-%m-%Y") if terakhir_date else None,
            }
            continue

        sudah_due = False
        for _, hasil_jam in info["draws"]:
            jam, menit = map(int, hasil_jam.split(":"))
            hasil_dt = now.replace(hour=jam, minute=menit, second=0, microsecond=0)
            batas = hasil_dt + timedelta(minutes=TOLERANSI_TELAT_MENIT)
            if now >= batas:
                sudah_due = True
                break

        telat = None if not sudah_due else (terakhir_date != today)

        status[kode] = {
            "nama": info["nama"],
            "tutup": tutup_str,
            "hasil": hasil_str_gabungan,
            "telat": telat,
            "terakhir": terakhir_date.strftime("%d-%m-%Y") if terakhir_date else None,
        }

    return status


def get_kode_pasaran_countdown():
    """
    Jam hasil BERIKUTNYA untuk tiap kode pasaran di JADWAL_PASARAN
    -- dipakai buat urutan "paling cepat result dulu" + label jam
    statis di dropdown Ddwn1 (analisis_waktu). Bukan countdown yang
    perlu di-refresh tiap detik -- cuma dihitung sekali per request
    halaman, ditulis apa adanya.

    Return: {kode: {"detik": int (>=0), "jam_label": str}}
    jam_label contoh: "22:15" (hari ini), "Besok 03:00", atau
    "Senin 17:45" (lebih dari besok -- pasaran yang harinya
    terbatas, mis. hari ini libur & baru aktif lagi beberapa hari
    lagi).

    Cari maju sampai 8 hari ke depan (cukup untuk jadwal mingguan
    yang ada -- pasaran paling jarang tetap muncul beberapa kali
    seminggu). Kode yang somehow tidak ketemu dalam 8 hari
    (seharusnya tidak terjadi) dilewati, tidak masuk hasil.
    """
    if WIB is None:
        return {}

    now = datetime.now(WIB)
    hasil = {}

    for kode, info in JADWAL_PASARAN.items():
        earliest = None
        earliest_delta_hari = None

        for delta_hari in range(8):
            tanggal_cek = now.date() + timedelta(days=delta_hari)
            weekday_name = HARI_INDO[tanggal_cek.weekday()]

            if info["hari"] is not None and weekday_name not in info["hari"]:
                continue

            for _, hasil_jam in info["draws"]:
                jam, menit = map(int, hasil_jam.split(":"))
                candidate = datetime.combine(tanggal_cek, dtime(jam, menit), tzinfo=WIB)

                if candidate > now and (earliest is None or candidate < earliest):
                    earliest = candidate
                    earliest_delta_hari = delta_hari

            if earliest is not None:
                break

        if earliest is None:
            continue

        jam_str = earliest.strftime("%H:%M")
        if earliest_delta_hari == 0:
            jam_label = jam_str
        elif earliest_delta_hari == 1:
            jam_label = f"Besok {jam_str}"
        else:
            jam_label = f"{HARI_INDO[earliest.weekday()]} {jam_str}"

        hasil[kode] = {
            "detik": int((earliest - now).total_seconds()),
            "jam_label": jam_label,
        }

    return hasil
