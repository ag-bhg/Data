import os
import json
from datetime import datetime, timezone

import firebase_admin
from firebase_admin import credentials, firestore

COLLECTION_NAME = "history"

_db = None


def init_db():
    global _db

    if _db is not None:
        return

    if not firebase_admin._apps:
        service_account_json = os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON")

        if not service_account_json:
            raise RuntimeError(
                "FIREBASE_SERVICE_ACCOUNT_JSON belum diset"
            )

        service_account_info = json.loads(service_account_json)

        cred = credentials.Certificate(service_account_info)
        firebase_admin.initialize_app(cred)

    _db = firestore.client()


def _get_db():
    if _db is None:
        init_db()
    return _db


def _document_id(row):
    """
    Membuat ID unik berdasarkan tanggal + periode + nomor.
    Ini menggantikan UNIQUE(tanggal, periode, nomor) di SQLite.
    """
    tanggal = str(row["tanggal"])
    periode = str(row["periode"])
    nomor = str(row["nomor"])

    return f"{tanggal}_{periode}_{nomor}".replace("/", "-")


def upsert_rows(rows):
    db = _get_db()
    changed = 0

    for row in rows:
        doc_id = _document_id(row)
        doc_ref = db.collection(COLLECTION_NAME).document(doc_id)

        existing = doc_ref.get()

        data = {
            "tanggal": row["tanggal"],
            "periode": row["periode"],
            "nomor": row["nomor"],
            "source_url": row.get("source_url"),
            "updated_at": firestore.SERVER_TIMESTAMP,
        }

        if not existing.exists:
            data["created_at"] = firestore.SERVER_TIMESTAMP

        doc_ref.set(data, merge=True)
        changed += 1

    return changed


def get_rows(page, per_page):
    db = _get_db()

    offset = (page - 1) * per_page

    query = (
        db.collection(COLLECTION_NAME)
        .order_by("created_at", direction=firestore.Query.DESCENDING)
        .limit(per_page)
    )

    # Firestore tidak memakai OFFSET seperti SQLite.
    # Untuk menjaga kompatibilitas dengan aplikasi sederhana ini,
    # kita ambil data lalu lakukan pagination di Python.
    all_docs = (
        db.collection(COLLECTION_NAME)
        .order_by("created_at", direction=firestore.Query.DESCENDING)
        .stream()
    )

    rows = []

    for doc in all_docs:
        data = doc.to_dict()

        rows.append({
            "tanggal": data.get("tanggal"),
            "periode": data.get("periode"),
            "nomor": data.get("nomor"),
            "source_url": data.get("source_url"),
        })

    return rows[offset:offset + per_page]


def count_rows():
    db = _get_db()

    docs = db.collection(COLLECTION_NAME).stream()
    return sum(1 for _ in docs)
