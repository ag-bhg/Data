import os
import re
import json

import firebase_admin
from firebase_admin import credentials, firestore


# =========================================================
# DTB2 = FIRESTORE
# =========================================================

def get_firestore_db():
    app_name = "dtb2"

    try:
        app = firebase_admin.get_app(app_name)

    except ValueError:
        firebase_json = os.environ.get(
            "FIREBASE_CREDENTIALS"
        )

        if not firebase_json:
            raise RuntimeError(
                "FIREBASE_CREDENTIALS belum diset"
            )

        cred = credentials.Certificate(
            json.loads(firebase_json)
        )

        app = firebase_admin.initialize_app(
            cred,
            name=app_name
        )

    return firestore.client(app=app)


def extract_kode_pasaran(periode):
    """
    Kolom "periode" berisi kode pasaran + nomor urut, mis:
    "TTM 22:00-604" -> kode pasaran "TTM 22:00"
    "OR2-606"        -> kode pasaran "OR2"
    Ambil bagian sebelum tanda "-NNN" di paling akhir.

    (Dipindah ke sini dari app.py supaya scraper.py juga bisa
    pakai fungsi yang SAMA PERSIS untuk deteksi data baru per
    pasaran -- lihat compute_group_sort_key/get_pasaran_progress
    di bawah. Sebelumnya app.py dan scraper.py punya potensi
    beda logic kalau salah satu diubah tanpa yang lain.)
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
    Sort key KHUSUS PER-PASARAN (beda dengan compute_sort_key di
    bawah yang gabungan semua pasaran jadi satu teks periode).

    Dipakai untuk bandingkan progres SATU kode pasaran dengan
    progres pasaran itu SENDIRI di sync sebelumnya -- bukan
    dibandingkan silang dengan kode pasaran lain (baca kenapa ini
    penting di catatan BUG di get_pasaran_progress()).

    "urutan" di sini nomor urut asli (int) dari periode, di-zfill
    supaya perbandingan string tetap urut angka, bukan alfabet.
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


def get_pasaran_progress():
    """
    Ambil progres TERAKHIR per kode pasaran -- {kode: sort_key},
    BUKAN satu sort_key global gabungan semua pasaran.

    =====================================================
    KENAPA INI PERBAIKAN DARI get_max_sort_key():
    get_max_sort_key() lama menyimpan SATU nilai "sort_key
    tertinggi" gabungan dari SEMUA kode pasaran (sort_key = teks
    tanggal + teks periode APA ADANYA, termasuk nama kode
    pasarannya). Karena perbandingannya string, nama kode
    pasaran ikut menentukan "besar-kecil" walau itu tidak ada
    hubungannya dengan waktu:

        "2026-09-01_KK-1223"  <  "2026-09-01_YSLM-612"
        (K < Y secara alfabet -- padahal KK-1223 bisa saja draw
        yang lebih baru dari YSLM-612)

    Begitu satu kode berhuruf awal "besar" (YSLM, WDM, VIRD, dst)
    pernah tersimpan di tanggal itu, known_max_sort_key global
    naik ke nilai itu. Setelah itu SEMUA draw baru dari kode
    berhuruf awal lebih kecil (KK, CRD, BLRS, DELD, dst) akan
    selalu kalah dibanding known_max_sort_key global -- dianggap
    "sudah lama", padahal belum pernah tersimpan sama sekali.
    Makanya kode-kode itu terlihat "macet"/tidak pernah nambah
    walau situs sumbernya sudah update.

    PERBAIKAN: lacak progres SATU-SATU per kode pasaran, dan
    bandingkan draw baru HANYA dengan progres kode itu sendiri
    (compute_group_sort_key, berbasis nomor urut asli, bukan
    nama kode). Kode pasaran lain tidak lagi ikut memblokir.
    =====================================================

    Disimpan di collection kecil terpisah "sync_state" (1 dokumen
    per kode pasaran, cuma berisi sort_key), BUKAN dihitung ulang
    dari collection "history" yang besar -- supaya tetap murah
    (~jumlah kode pasaran aktif, bukan ribuan dokumen histori).
    """
    db = get_firestore_db()

    progress = {}
    for doc in db.collection("sync_state").stream():
        data = doc.to_dict() or {}
        sort_key = data.get("sort_key")
        if sort_key:
            progress[doc.id] = sort_key

    return progress


def update_pasaran_progress(rows, known_progress):
    """
    Majukan progres per-pasaran di collection "sync_state"
    berdasarkan baris yang BENAR-BENAR terbaca di batch ini
    (all_rows dari scraper.py -- termasuk yang ternyata sudah
    lama, supaya "garis depan" tiap pasaran ikut representatif).

    known_progress: hasil get_pasaran_progress() SEBELUM batch
    ini diproses (snapshot). Progres HANYA PERNAH NAIK, tidak
    pernah mundur -- doc kode yang tidak berubah tidak ditulis
    ulang (hemat kuota write).

    Baris dengan periode yang tidak bisa dipecah jadi (kode,
    urutan) -- extract_urutan_pasaran mengembalikan None --
    dilewati di sini (tidak ikut menentukan progres), tapi tetap
    AMAN karena scraper.py memang sengaja selalu menganggap baris
    seperti itu "baru" (lihat sync_history), bukan pernah
    terblokir gara-gara tidak tercatat progresnya di sini.
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

    db = get_firestore_db()
    batch = db.batch()

    for kode, group_key in to_write.items():
        doc_ref = db.collection("sync_state").document(kode)
        batch.set(doc_ref, {"sort_key": group_key}, merge=True)

    batch.commit()

    return len(to_write)


def compute_sort_key(tanggal, periode):
    """
    Bikin string yang bisa diurutkan langsung oleh Firestore
    (order_by), karena "tanggal" disimpan dalam format
    dd-mm-yyyy yang urutan aslinya tidak kronologis.

    Dipakai juga oleh scraper.py untuk logika "catch-up" sync
    (lihat get_max_sort_key di bawah).
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


def upsert_rows(rows):

    db = get_firestore_db()

    firestore_changed = 0

    # =====================================================
    # HILANGKAN DUPLIKAT DATA YANG MASUK
    #
    # Berdasarkan:
    # tanggal + periode + nomor
    # =====================================================

    unique_rows = {}

    for row in rows:

        tanggal = str(row["tanggal"]).strip()
        periode = str(row["periode"]).strip()
        nomor = str(row["nomor"]).strip()

        key = (tanggal, periode, nomor)

        if key not in unique_rows:
            unique_rows[key] = row

    rows = list(unique_rows.values())


    # =====================================================
    # SIMPAN KE FIRESTORE / DTB2
    #
    # CATATAN PERBAIKAN:
    # Versi lama melakukan doc_ref.get() untuk tiap baris guna
    # mengecek apakah datanya berubah -> ini menghabiskan 1 READ
    # per baris di setiap sinkronisasi (di luar dari boros read
    # di get_rows/count_rows, ini ikut menambah kuota terpakai).
    # Karena doc_id sudah unik dari tanggal+periode+nomor, dan
    # data historis draw pada dasarnya tidak berubah setelah
    # ditulis, kita langsung merge=True tanpa membaca dulu.
    # Ini menghilangkan seluruh read di proses sync, hanya
    # menyisakan write (kuota write jauh lebih longgar).
    # =====================================================

    batch = db.batch()
    batch_count = 0

    for row in rows:

        tanggal = str(row["tanggal"]).strip()
        periode = str(row["periode"]).strip()
        nomor = str(row["nomor"]).strip()

        doc_id = f"{tanggal}_{periode}_{nomor}"
        doc_ref = db.collection("history").document(doc_id)

        new_data = {
            "tanggal": tanggal,
            "periode": periode,
            "nomor": nomor,
            "source_url": row.get("source_url"),
            "sort_key": compute_sort_key(tanggal, periode),
            # Disimpan terpisah (bukan cuma diturunkan on-the-fly dari
            # "periode" tiap kali baca) supaya bisa dipakai sebagai
            # filter langsung di query Firestore (.where("kode_pasaran", "==", ...))
            # -- lihat get_rows()/count_rows() dan dropdown periode di "/".
            "kode_pasaran": extract_kode_pasaran(periode),
            "updated_at": firestore.SERVER_TIMESTAMP,
        }

        batch.set(doc_ref, new_data, merge=True)
        batch_count += 1
        firestore_changed += 1

        # Firestore batasi 500 operasi per batch.
        if batch_count >= 400:
            batch.commit()
            batch = db.batch()
            batch_count = 0

    if batch_count > 0:
        batch.commit()

    return firestore_changed


def init_db():

    get_firestore_db()


def get_rows(page, per_page, kode=None):
    """
    PERBAIKAN: sebelumnya fungsi ini men-stream SELURUH koleksi
    "history" lalu memotongnya di Python (rows[offset:offset+per_page]).
    Itu artinya setiap kali halaman dibuka, SEMUA dokumen ikut
    kena biaya read walau yang ditampilkan cuma `per_page` baris.
    Sekarang pagination dilakukan di level Firestore (order_by +
    offset + limit) sehingga hanya dokumen yang benar-benar
    ditampilkan yang dibaca (plus dokumen yang dilewati offset,
    jauh lebih sedikit dibanding seluruh koleksi).

    Diurutkan berdasarkan sort_key (tanggal draw + periode) — ini
    yang dipakai fitur backend (catch-up sync, deteksi gap nomor
    urut, dll) yang memang butuh urutan kronologis draw, BUKAN
    kapan datanya disimpan. Untuk tampilan "Draw History" di
    halaman utama, pakai get_rows_by_recency() di bawah supaya
    tidak keurut alfabet nama pasaran waktu tanggalnya sama.

    kode: opsional, kode pasaran (mis. "OR2", "TTM 22:00") dari
    dropdown periode di "/". Kalau diisi, query difilter pakai
    field "kode_pasaran" (lihat upsert_rows) supaya pagination
    tetap dilakukan di level Firestore (bukan filter manual di
    Python) walau sedang menampilkan satu periode saja.

    CATATAN DEPLOY: filter kode_pasaran + order_by sort_key
    berbarengan butuh composite index di Firestore. Kalau belum
    ada, Firestore akan menolak query ini dan memberi error yang
    isinya link untuk membuat index tersebut otomatis -- tinggal
    dibuka sekali saja.
    """

    db = get_firestore_db()

    offset = (page - 1) * per_page

    query = db.collection("history")

    if kode:
        query = query.where("kode_pasaran", "==", kode)

    query = (
        query
        .order_by("sort_key", direction=firestore.Query.DESCENDING)
        .offset(offset)
        .limit(per_page)
    )

    rows = []

    for doc in query.stream():
        data = doc.to_dict()

        rows.append({
            "tanggal": data.get("tanggal"),
            "periode": data.get("periode"),
            "nomor": data.get("nomor"),
            "source_url": data.get("source_url"),
        })

    return rows


def get_rows_by_recency(page, per_page):
    """
    Sama seperti get_rows(), tapi diurutkan berdasarkan
    `updated_at` (timestamp asli Firestore, saat dokumen terakhir
    ditulis/disentuh), bukan sort_key (tanggal draw + periode).

    Dipakai KHUSUS untuk halaman "Draw History" di "/" supaya
    baris yang benar-benar baru disinkronkan tampil paling atas —
    sebelumnya, karena banyak draw share tanggal yang sama,
    urutan sort_key jatuh ke teks periode (alfabet nama pasaran),
    bukan urutan waktu sebenarnya.
    """

    db = get_firestore_db()

    offset = (page - 1) * per_page

    query = (
        db.collection("history")
        .order_by("updated_at", direction=firestore.Query.DESCENDING)
        .offset(offset)
        .limit(per_page)
    )

    rows = []

    for doc in query.stream():
        data = doc.to_dict()

        rows.append({
            "tanggal": data.get("tanggal"),
            "periode": data.get("periode"),
            "nomor": data.get("nomor"),
            "source_url": data.get("source_url"),
        })

    return rows


def count_rows(kode=None):
    """
    PERBAIKAN: sebelumnya men-stream SELURUH koleksi hanya untuk
    menghitung jumlah dokumen. Sekarang pakai Firestore
    aggregation query count(), yang dihitung di server Firestore
    tanpa mengunduh tiap dokumen satu per satu.

    kode: opsional, sama seperti di get_rows() -- kalau diisi,
    hitung total HANYA untuk periode itu (dipakai untuk info
    "Total data tersimpan" dan hitung jumlah halaman saat dropdown
    periode sedang aktif).
    """

    db = get_firestore_db()

    query = db.collection("history")

    if kode:
        query = query.where("kode_pasaran", "==", kode)

    result = query.count().get()

    return result[0][0].value


def get_kode_pasaran_options():
    """
    Daftar semua kode pasaran yang tersedia, untuk isi dropdown
    periode di "/" (dibuat SEPERTI dropdown pasaran di situs
    sumber, lihat scraper.py SOURCE_URL).

    Diambil dari collection "sync_state" (1 dokumen per kode
    pasaran, sudah ada duluan untuk keperluan lain -- lihat
    get_pasaran_progress()), BUKAN dari scan koleksi "history"
    yang besar. Jadi cuma perlu baca sejumlah kode pasaran aktif,
    bukan ribuan dokumen histori.
    """

    db = get_firestore_db()

    kode_list = [doc.id for doc in db.collection("sync_state").stream()]

    return sorted(kode_list)


def migrate_kode_pasaran(batch_size=400):
    """
    JALANKAN SEKALI SAJA setelah deploy fitur dropdown periode,
    untuk mengisi field "kode_pasaran" pada dokumen LAMA yang
    dibuat sebelum field ini ada (dokumen lama tidak akan cocok
    dengan filter .where("kode_pasaran", "==", ...) di get_rows()
    sampai field-nya terisi).

    Sama polanya dengan migrate_sort_keys() di atas -- satu kali
    full-scan, lalu tidak perlu dipanggil lagi. Panggil lewat
    endpoint /api/migrate-kode-pasaran (lihat app.py).
    """

    db = get_firestore_db()
    docs = db.collection("history").stream()

    batch = db.batch()
    batch_count = 0
    updated = 0

    for doc in docs:
        data = doc.to_dict() or {}

        if "kode_pasaran" in data:
            continue

        kode = extract_kode_pasaran(data.get("periode"))
        batch.set(doc.reference, {"kode_pasaran": kode}, merge=True)

        batch_count += 1
        updated += 1

        if batch_count >= batch_size:
            batch.commit()
            batch = db.batch()
            batch_count = 0

    if batch_count > 0:
        batch.commit()

    return updated


def get_max_sort_key():
    """
    Ambil sort_key TERBESAR (data terbaru) yang sudah tersimpan.
    Dipakai scraper.py untuk tahu kapan proses sync boleh berhenti:
    begitu halaman yang di-scrape isinya sudah <= nilai ini semua,
    berarti sudah "mengejar" sampai data yang sudah pernah
    tersimpan sebelumnya -> tidak ada data yang terlewat walau
    sudah lama tidak sync.

    Cuma 1 read (limit 1), bukan baca seluruh koleksi.
    Return None kalau koleksi masih kosong.
    """

    db = get_firestore_db()

    query = (
        db.collection("history")
        .order_by("sort_key", direction=firestore.Query.DESCENDING)
        .limit(1)
    )

    for doc in query.stream():
        data = doc.to_dict() or {}
        return data.get("sort_key")

    return None


def migrate_sort_keys(batch_size=400):
    """
    JALANKAN SEKALI SAJA setelah deploy versi ini, untuk mengisi
    field "sort_key" pada dokumen LAMA yang dibuat sebelum
    perbaikan ini (dokumen lama tidak punya field ini, sehingga
    tidak akan muncul di hasil order_by("sort_key") pada get_rows).

    Fungsi ini melakukan satu kali full-scan (biaya read sekali
    saja, tidak berulang), lalu setelahnya tidak perlu dipanggil
    lagi. Panggil lewat endpoint /api/migrate-sort-key (lihat
    app.py) atau jalankan manual dari lingkungan lokal.
    """

    db = get_firestore_db()
    docs = db.collection("history").stream()

    batch = db.batch()
    batch_count = 0
    updated = 0

    for doc in docs:
        data = doc.to_dict() or {}

        if "sort_key" in data:
            continue

        key = compute_sort_key(data.get("tanggal"), data.get("periode"))
        batch.set(doc.reference, {"sort_key": key}, merge=True)

        batch_count += 1
        updated += 1

        if batch_count >= batch_size:
            batch.commit()
            batch = db.batch()
            batch_count = 0

    if batch_count > 0:
        batch.commit()

    return updated
