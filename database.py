import os
import re

import psycopg2
import psycopg2.extras


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
    """
    dsn = os.environ.get("DATABASE_URL")

    if not dsn:
        raise RuntimeError("DATABASE_URL belum diset")

    return psycopg2.connect(
        dsn,
        sslmode="require",
        cursor_factory=psycopg2.extras.RealDictCursor,
    )


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
        conn.commit()
    finally:
        conn.close()


def get_pasaran_progress():
    """
    Ambil progres TERAKHIR per kode pasaran -- {kode: sort_key}.
    Sama perannya dengan versi Firestore, cuma sumbernya sekarang
    tabel "sync_state" di Postgres.
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT kode_pasaran, sort_key FROM sync_state;")
            rows = cur.fetchall()
        return {r["kode_pasaran"]: r["sort_key"] for r in rows}
    finally:
        conn.close()


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


def upsert_rows(rows):
    """
    Simpan baris baru. Dedup dalam batch berdasarkan
    tanggal+periode+nomor sama seperti sebelumnya, lalu ditulis
    lewat INSERT ... ON CONFLICT DO UPDATE (constraint UNIQUE di
    init_db() berperan sama seperti doc_id deterministik di
    Firestore dulu -- baris yang sama persis aman ditulis ulang,
    tidak akan dobel).
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

    try:
        with conn.cursor() as cur:
            for row in rows:
                tanggal = str(row["tanggal"]).strip()
                periode = str(row["periode"]).strip()
                nomor = str(row["nomor"]).strip()

                sort_key = compute_sort_key(tanggal, periode)
                kode = extract_kode_pasaran(periode)

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
        conn.commit()
    finally:
        conn.close()

    return changed


def get_rows(page, per_page, kode=None):
    """
    Ambil satu halaman baris, diurutkan sort_key DESC (tanggal
    draw + periode -- dipakai fitur backend yang butuh urutan
    kronologis draw). Filter opsional per kode pasaran, dipakai
    dropdown periode di "/".

    LIMIT/OFFSET native Postgres -- tidak ada lagi drama composite
    index seperti Firestore, cukup index (kode_pasaran, sort_key)
    yang sudah dibuat di init_db().
    """

    offset = (page - 1) * per_page

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
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
    finally:
        conn.close()


def get_rows_by_recency(page, per_page):
    """
    Sama seperti get_rows(), tapi diurutkan berdasarkan
    updated_at (kapan baris terakhir ditulis/disentuh), bukan
    sort_key. Dipertahankan untuk kompatibilitas -- tidak dipanggil
    di alur utama saat ini.
    """

    offset = (page - 1) * per_page

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
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
    finally:
        conn.close()


def count_rows(kode=None):
    """
    Hitung total baris (opsional per kode pasaran). COUNT(*) biasa
    -- tidak ada lagi konsep "kuota per 1000 entri index" seperti
    aggregation query Firestore, dan untuk ukuran data proyek ini
    (belasan ribu baris) tetap cepat tanpa perlu index tambahan.
    """

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            if kode:
                cur.execute(
                    "SELECT COUNT(*) AS total FROM history WHERE kode_pasaran = %s;",
                    (kode,),
                )
            else:
                cur.execute("SELECT COUNT(*) AS total FROM history;")
            row = cur.fetchone()
        return row["total"]
    finally:
        conn.close()


def get_kode_pasaran_options():
    """
    Daftar semua kode pasaran yang tersedia, untuk isi dropdown
    periode di "/". Diambil dari tabel "sync_state" (kecil, 1 baris
    per kode pasaran), bukan scan tabel "history" yang besar.
    """

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT kode_pasaran FROM sync_state ORDER BY kode_pasaran;")
            rows = cur.fetchall()
        return [r["kode_pasaran"] for r in rows]
    finally:
        conn.close()


def get_max_sort_key():
    """
    Ambil sort_key TERBESAR (data terbaru) yang sudah tersimpan.
    Dipertahankan untuk kompatibilitas -- tidak dipanggil di alur
    utama saat ini (scraper.py pakai get_pasaran_progress()).
    """

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT sort_key FROM history ORDER BY sort_key DESC LIMIT 1;"
            )
            row = cur.fetchone()
        return row["sort_key"] if row else None
    finally:
        conn.close()
