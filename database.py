import os
import json
import re
from datetime import datetime, timezone, timedelta

import firebase_admin
from firebase_admin import credentials, firestore, db as realtime_db


# =========================================================
# DTB2 = Firestore
# =========================================================

def get_firestore_db():
    app_name = "dtb2"

    try:
        app = firebase_admin.get_app(app_name)
    except ValueError:
        firebase_json = os.environ.get("FIREBASE_CREDENTIALS")

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


# =========================================================
# DTB1 = Realtime Database
# =========================================================

def get_rtdb():
    app_name = "dtb1"

    try:
        app = firebase_admin.get_app(app_name)
    except ValueError:
        firebase_json = os.environ.get(
            "FIREBASE_DTB1_CREDENTIALS"
        )

        database_url = os.environ.get(
            "FIREBASE_DTB1_DATABASE_URL"
        )

        if not firebase_json:
            raise RuntimeError(
                "FIREBASE_DTB1_CREDENTIALS belum diset"
            )

        if not database_url:
            raise RuntimeError(
                "FIREBASE_DTB1_DATABASE_URL belum diset"
            )

        cred = credentials.Certificate(
            json.loads(firebase_json)
        )

        app = firebase_admin.initialize_app(
            cred,
            {
                "databaseURL": database_url
            },
            name=app_name
        )

    return realtime_db.reference(app=app)


# =========================================================
# Waktu Indonesia
# =========================================================

def current_saved_at():
    jakarta = timezone(timedelta(hours=7))

    return datetime.now(jakarta).strftime(
        "%d/%m/%Y, %H.%M"
    )


# =========================================================
# Ambil ID utama
#
# HKL-605  -> HKL
# YSLM-607 -> YSLM
# KK-1209  -> KK
# =========================================================

def get_main_id(periode):
    periode = str(periode).strip()

    if "-" not in periode:
        return periode

    return periode.split("-", 1)[0].strip()


# =========================================================
# Pecah format data DTB1
#
# Contoh:
# 25-08-2026 KK-1209 6315
# 24-08-2026 KK-1208 3278
# =========================================================

def parse_data_entries(data_string):
    if not data_string:
        return []

    pattern = re.compile(
        r"(\d{2}-\d{2}-\d{4})\s+"
        r"(\S+)\s+"
        r"(\d{4})"
    )

    return pattern.findall(str(data_string))


# =========================================================
# Bentuk satu entry dengan FORMAT LAMA
# =========================================================

def make_entry(row):
    tanggal = str(row["tanggal"]).strip()
    periode = str(row["periode"]).strip()
    nomor = str(row["nomor"]).strip()

    return f"{tanggal} {periode} {nomor}"


# =========================================================
# Cari node name di DTB1
#
# Struktur:
#
# savedData
#   0
#     name: HKL
#     data: ...
#   1
#     name: KK
#     data: ...
# =========================================================

def find_dtb1_node(root_ref, main_id):
    saved_ref = root_ref.child("savedData")

    saved_data = saved_ref.get()

    if not saved_data:
        return None, None

    # savedData biasanya berupa list
    if isinstance(saved_data, list):

        for index, item in enumerate(saved_data):
            if not isinstance(item, dict):
                continue

            name = str(item.get("name", "")).strip()

            if name == main_id:
                return saved_ref.child(str(index)), item

        return None, None

    # Pengaman jika suatu saat Firebase mengembalikan dictionary
    if isinstance(saved_data, dict):

        for key, item in saved_data.items():
            if not isinstance(item, dict):
                continue

            name = str(item.get("name", "")).strip()

            if name == main_id:
                return saved_ref.child(str(key)), item

    return None, None


# =========================================================
# Cari index kosong berikutnya
# =========================================================

def get_next_saved_data_index(saved_data):
    if not saved_data:
        return 0

    if isinstance(saved_data, list):
        return len(saved_data)

    if isinstance(saved_data, dict):

        numeric_keys = []

        for key in saved_data.keys():
            try:
                numeric_keys.append(int(key))
            except (ValueError, TypeError):
                pass

        if not numeric_keys:
            return 0

        return max(numeric_keys) + 1

    return 0


# =========================================================
# UPDATE DTB1
# =========================================================

def sync_to_dtb1(rows):
    root_ref = get_rtdb()

    changed = 0
    skipped = 0
    created = 0

    # Proses dari data terbaru
    for row in rows:

        tanggal = str(row["tanggal"]).strip()
        periode = str(row["periode"]).strip()
        nomor = str(row["nomor"]).strip()

        main_id = get_main_id(periode)

        new_entry = make_entry(row)

        node_ref, existing = find_dtb1_node(
            root_ref,
            main_id
        )

        # =================================================
        # ID BELUM ADA → BUAT BARU
        # =================================================

        if node_ref is None:

            saved_ref = root_ref.child("savedData")

            saved_data = saved_ref.get()

            next_index = get_next_saved_data_index(
                saved_data
            )

            new_ref = saved_ref.child(
                str(next_index)
            )

            new_ref.set({
                "data": new_entry,
                "name": main_id,
                "savedAt": current_saved_at()
            })

            created += 1
            changed += 1

            continue

        # =================================================
        # ID SUDAH ADA
        # =================================================

        old_data = str(
            existing.get("data", "")
        ).strip()

        # Cek anti-duplikat
        existing_entries = parse_data_entries(
            old_data
        )

        duplicate = False

        for old_tanggal, old_periode, old_nomor in existing_entries:

            if (
                old_tanggal == tanggal
                and old_periode == periode
                and old_nomor == nomor
            ):
                duplicate = True
                break

        if duplicate:
            skipped += 1
            continue

        # =================================================
        # DATA BARU → TAMBAHKAN DI DEPAN
        #
        # FORMAT TIDAK DIUBAH
        # =================================================

        if old_data:
            new_data = new_entry + " " + old_data
        else:
            new_data = new_entry

        # Hanya update field yang memang perlu.
        # Field settings yang sudah ada TIDAK disentuh.
        node_ref.update({
            "data": new_data,
            "name": main_id,
            "savedAt": current_saved_at()
        })

        changed += 1

    return {
        "changed": changed,
        "created": created,
        "skipped": skipped
    }


# =========================================================
# SIMPAN DTB2 + SINKRON KE DTB1
# =========================================================

def upsert_rows(rows):
    db = get_firestore_db()

    changed = 0

    # ---------------------------------------------
    # 1. Simpan ke DTB2 seperti sistem sebelumnya
    # ---------------------------------------------

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

    # ---------------------------------------------
    # 2. Sinkron DTB2 → DTB1
    # ---------------------------------------------

    result = sync_to_dtb1(rows)

    return result["changed"]


# =========================================================
# INIT
# =========================================================

def init_db():
    get_firestore_db()
    get_rtdb()


# =========================================================
# BACA DATA UNTUK HALAMAN WEB
# =========================================================

def get_rows(page, per_page):
    db = get_firestore_db()

    offset = (page - 1) * per_page

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


# =========================================================
# HITUNG DATA
# =========================================================

def count_rows():
    db = get_firestore_db()

    docs = db.collection("history").stream()

    return sum(1 for _ in docs)
