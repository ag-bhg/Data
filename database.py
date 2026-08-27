import os
import json
import firebase_admin
from firebase_admin import credentials, firestore

def get_db():
    if not firebase_admin._apps:
        firebase_json = os.environ.get("FIREBASE_CREDENTIALS")

        if not firebase_json:
            raise RuntimeError("FIREBASE_CREDENTIALS belum diset")

        cred = credentials.Certificate(json.loads(firebase_json))
        firebase_admin.initialize_app(cred)

    return firestore.client()


def init_db():
    # Firestore tidak membutuhkan CREATE TABLE.
    # Collection akan dibuat otomatis saat data pertama disimpan.
    get_db()


def upsert_rows(rows):
    db = get_db()
    changed = 0

    for row in rows:
        tanggal = str(row["tanggal"])
        periode = str(row["periode"])
        nomor = str(row["nomor"])

        doc_id = f"{tanggal}_{periode}_{nomor}"

        data = {
            "tanggal": tanggal,
            "periode": periode,
            "nomor": nomor,
            "source_url": row.get("source_url"),
            "updated_at": firestore.SERVER_TIMESTAMP
        }

        db.collection("history").document(doc_id).set(
            data,
            merge=True
        )

        changed += 1

    return changed


def get_rows(page, per_page):
    db = get_db()

    offset = (page - 1) * per_page

    query = (
        db.collection("history")
        .order_by("updated_at", direction=firestore.Query.DESCENDING)
        .limit(per_page)
    )

    # Untuk sementara ambil data lalu lakukan pagination.
    # Cocok untuk jumlah data kecil/menengah.
    docs = db.collection("history").stream()

    rows = []

    for doc in docs:
        data = doc.to_dict()

        rows.append({
            "tanggal": data.get("tanggal"),
            "periode": data.get("periode"),
            "nomor": data.get("nomor"),
            "source_url": data.get("source_url")
        })

    rows.sort(
        key=lambda x: (
            x.get("tanggal") or "",
            x.get("periode") or ""
        ),
        reverse=True
    )

    return rows[offset:offset + per_page]


def count_rows():
    db = get_db()

    docs = db.collection("history").stream()

    return sum(1 for _ in docs)
