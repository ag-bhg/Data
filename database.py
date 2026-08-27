import os
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


def _sort_key(tanggal, periode):
    """
    Bikin string yang bisa diurutkan langsung oleh Firestore
    (order_by), karena "tanggal" disimpan dalam format
    dd-mm-yyyy yang urutan aslinya tidak kronologis.
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
            "sort_key": _sort_key(tanggal, periode),
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


def get_rows(page, per_page):
    """
    PERBAIKAN: sebelumnya fungsi ini men-stream SELURUH koleksi
    "history" lalu memotongnya di Python (rows[offset:offset+per_page]).
    Itu artinya setiap kali halaman dibuka, SEMUA dokumen ikut
    kena biaya read walau yang ditampilkan cuma `per_page` baris.
    Sekarang pagination dilakukan di level Firestore (order_by +
    offset + limit) sehingga hanya dokumen yang benar-benar
    ditampilkan yang dibaca (plus dokumen yang dilewati offset,
    jauh lebih sedikit dibanding seluruh koleksi).
    """

    db = get_firestore_db()

    offset = (page - 1) * per_page

    query = (
        db.collection("history")
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


def count_rows():
    """
    PERBAIKAN: sebelumnya men-stream SELURUH koleksi hanya untuk
    menghitung jumlah dokumen. Sekarang pakai Firestore
    aggregation query count(), yang dihitung di server Firestore
    tanpa mengunduh tiap dokumen satu per satu.
    """

    db = get_firestore_db()

    result = db.collection("history").count().get()

    return result[0][0].value


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

        key = _sort_key(data.get("tanggal"), data.get("periode"))
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
