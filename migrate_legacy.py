import os
import json

import firebase_admin
from firebase_admin import credentials, firestore


# =========================================================
# MIGRASI SATU KALI: Firestore (dtb2 lama) -> Postgres (Neon)
#
# SENGAJA dipisah dari database.py supaya database.py yang dipakai
# alur normal (index, sync, dsb) tidak perlu bergantung ke
# firebase_admin sama sekali -- modul ini cuma dipakai selama masa
# transisi, lewat endpoint /api/migrate-to-postgres &
# /api/migrate-sync-state (lihat app.py), dan boleh dihapus kalau
# migrasi sudah selesai & terverifikasi.
#
# BUTUH KEDUA env var ini SEKALIGUS selama masa transisi:
# - FIREBASE_CREDENTIALS (punya lama, buat baca data sumber)
# - DATABASE_URL          (punya baru, buat tulis ke Postgres)
# =========================================================

def _get_firestore_db():
    app_name = "legacy_migration"

    try:
        app = firebase_admin.get_app(app_name)

    except ValueError:
        firebase_json = os.environ.get("FIREBASE_CREDENTIALS")

        if not firebase_json:
            raise RuntimeError(
                "FIREBASE_CREDENTIALS belum diset "
                "(dibutuhkan sementara, khusus untuk migrasi ini)"
            )

        cred = credentials.Certificate(json.loads(firebase_json))
        app = firebase_admin.initialize_app(cred, name=app_name)

    return firestore.client(app=app)


def migrate_history_batch(batch_size=500, cursor=None):
    """
    Pindahkan SATU BATCH dokumen dari collection "history" (Firestore)
    ke tabel "history" (Postgres). Dipanggil berulang lewat endpoint
    /api/migrate-to-postgres?cursor=... sampai "done": true.

    cursor: doc_id Firestore terakhir yang sudah diproses batch
    sebelumnya (buat lanjut kalau kepotong -- dibatasi timeout
    serverless atau kuota harian). None berarti mulai dari awal.

    Diurutkan pakai nama dokumen (__name__) supaya pagination-nya
    stabil (tidak ada baris yang kelewat/dobel walau dipanggil
    berkali-kali secara terpisah).

    Aman diulang: upsert_rows() di Postgres pakai ON CONFLICT DO
    UPDATE, jadi dokumen yang keproses ulang tidak jadi dobel.
    """
    from database import upsert_rows

    db = _get_firestore_db()

    query = (
        db.collection("history")
        .order_by("__name__")
        .limit(batch_size)
    )

    if cursor:
        last_doc = db.collection("history").document(cursor).get()
        if last_doc.exists:
            query = query.start_after(last_doc)

    docs = list(query.stream())

    rows = []
    for doc in docs:
        data = doc.to_dict() or {}
        rows.append({
            "tanggal": data.get("tanggal"),
            "periode": data.get("periode"),
            "nomor": data.get("nomor"),
            "source_url": data.get("source_url"),
        })

    moved = upsert_rows(rows) if rows else 0

    next_cursor = docs[-1].id if len(docs) == batch_size else None

    return {
        "moved": moved,
        "done": next_cursor is None,
        "next_cursor": next_cursor,
    }


def migrate_sync_state():
    """
    Pindahkan seluruh collection "sync_state" (kecil, ~jumlah kode
    pasaran aktif) ke tabel "sync_state" di Postgres. Cukup sekali
    full-scan, tidak perlu dibatch seperti "history".
    """
    from database import set_sync_state_raw

    db = _get_firestore_db()

    count = 0
    for doc in db.collection("sync_state").stream():
        data = doc.to_dict() or {}
        sort_key = data.get("sort_key")

        if sort_key:
            set_sync_state_raw(doc.id, sort_key)
            count += 1

    return count
