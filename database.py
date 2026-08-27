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

    return pattern.findall(
        str(data_string)
    )


# =========================================================
# Bentuk satu entry dengan FORMAT LAMA
# =========================================================

def make_entry(row):

    tanggal = str(
        row["tanggal"]
    ).strip()

    periode = str(
        row["periode"]
    ).strip()

    nomor = str(
        row["nomor"]
    ).strip()

    return f"{tanggal} {periode} {nomor}"


# =========================================================
# UPDATE DTB1
#
# Tujuan:
# - Tidak membuat ID utama ganda
# - Tidak menambahkan data yang sama
# - Membersihkan duplikat lama
# - Mempertahankan field settings
# - Menyimpan savedData sekali
# =========================================================

def sync_to_dtb1(rows):

    root_ref = get_rtdb()
    saved_ref = root_ref.child("savedData")

    changed = 0
    skipped = 0
    created = 0

    # =====================================================
    # HILANGKAN DUPLIKAT DARI DATA YANG MASUK
    # =====================================================

    unique_rows = {}

    for row in rows:

        tanggal = str(
            row["tanggal"]
        ).strip()

        periode = str(
            row["periode"]
        ).strip()

        nomor = str(
            row["nomor"]
        ).strip()

        key = (
            tanggal,
            periode,
            nomor
        )

        if key not in unique_rows:
            unique_rows[key] = row

    rows = list(
        unique_rows.values()
    )

    # =====================================================
    # AMBIL savedData SEKALI
    # =====================================================

    saved_data = saved_ref.get()

    if not saved_data:
        saved_data = []

    # =====================================================
    # NORMALISASI savedData
    # =====================================================

    if isinstance(saved_data, dict):

        ordered = []

        for key, item in saved_data.items():

            if isinstance(item, dict):

                ordered.append(
                    (key, item)
                )

        ordered.sort(
            key=lambda x:
                int(x[0])
                if str(x[0]).isdigit()
                else 999999999
        )

        saved_data = [
            item
            for _, item in ordered
        ]

    elif not isinstance(saved_data, list):

        saved_data = []

    # =====================================================
    # PROSES DATA
    # =====================================================

    for row in rows:

        tanggal = str(
            row["tanggal"]
        ).strip()

        periode = str(
            row["periode"]
        ).strip()

        nomor = str(
            row["nomor"]
        ).strip()

        main_id = get_main_id(
            periode
        )

        new_entry = make_entry(
            row
        )

        # =================================================
        # CARI ID UTAMA
        # =================================================

        node_index = None

        for index, item in enumerate(
            saved_data
        ):

            if not isinstance(item, dict):
                continue

            name = str(
                item.get("name", "")
            ).strip()

            if name == main_id:

                node_index = index
                break

        # =================================================
        # ID BELUM ADA
        # =================================================

        if node_index is None:

            saved_data.append({

                "data": new_entry,

                "name": main_id,

                "savedAt":
                    current_saved_at()

            })

            created += 1
            changed += 1

            continue

        # =================================================
        # ID SUDAH ADA
        # =================================================

        item = saved_data[
            node_index
        ]

        old_data = str(
            item.get("data", "")
        ).strip()

        # =================================================
        # PECAH DATA LAMA
        # =================================================

        old_entries = parse_data_entries(
            old_data
        )

        unique_entries = []
        seen = set()

        # =================================================
        # BERSIHKAN DUPLIKAT LAMA
        # =================================================

        for (
            old_tanggal,
            old_periode,
            old_nomor
        ) in old_entries:

            key = (
                old_tanggal,
                old_periode,
                old_nomor
            )

            if key in seen:
                continue

            seen.add(key)

            unique_entries.append(
                f"{old_tanggal} "
                f"{old_periode} "
                f"{old_nomor}"
            )

        # =================================================
        # DATA BARU
        # =================================================

        new_key = (
            tanggal,
            periode,
            nomor
        )

        if new_key not in seen:

            unique_entries.insert(
                0,
                new_entry
            )

            seen.add(
                new_key
            )

        # =================================================
        # SUSUN DATA
        # =================================================

        new_data = " ".join(
            unique_entries
        )

        # =================================================
        # TIDAK ADA PERUBAHAN
        # =================================================

        if new_data == old_data:

            skipped += 1
            continue

        # =================================================
        # UPDATE
        #
        # Jangan mengganti settings.
        # Kita hanya mengubah:
        # data
        # name
        # savedAt
        # =================================================

        item["data"] = new_data

        item["name"] = main_id

        item["savedAt"] = (
            current_saved_at()
        )

        changed += 1

    # =====================================================
    # SIMPAN SEKALI SAJA
    # =====================================================

    if changed > 0:

        saved_ref.set(
            saved_data
        )

    return {
        "changed": changed,
        "created": created,
        "skipped": skipped
    }


# =========================================================
# SIMPAN DTB2 + SINKRON KE DTB1
#
# PENTING:
#
# Nilai return sekarang berdasarkan perubahan nyata
# di Firestore.
#
# Bukan berdasarkan perubahan DTB1.
# =========================================================

def upsert_rows(rows):

    db = get_firestore_db()

    firestore_changed = 0

    # =====================================================
    # HILANGKAN DUPLIKAT DATA MASUK
    # =====================================================

    unique_rows = {}

    for row in rows:

        tanggal = str(
            row["tanggal"]
        ).strip()

        periode = str(
            row["periode"]
        ).strip()

        nomor = str(
            row["nomor"]
        ).strip()

        key = (
            tanggal,
            periode,
            nomor
        )

        if key not in unique_rows:

            unique_rows[key] = row

    rows = list(
        unique_rows.values()
    )

    # =====================================================
    # SIMPAN KE FIRESTORE
    # =====================================================

    for row in rows:

        tanggal = str(
            row["tanggal"]
        ).strip()

        periode = str(
            row["periode"]
        ).strip()

        nomor = str(
            row["nomor"]
        ).strip()

        doc_id = (
            f"{tanggal}_"
            f"{periode}_"
            f"{nomor}"
        )

        doc_ref = (
            db.collection("history")
            .document(doc_id)
        )

        # =================================================
        # CEK DATA SUDAH ADA ATAU BELUM
        # =================================================

        existing_doc = doc_ref.get()

        new_data = {
            "tanggal": tanggal,
            "periode": periode,
            "nomor": nomor,
            "source_url":
                row.get("source_url")
        }

        # =================================================
        # DOKUMEN BARU
        # =================================================

        if not existing_doc.exists:

            new_data[
                "updated_at"
            ] = firestore.SERVER_TIMESTAMP

            doc_ref.set(
                new_data,
                merge=True
            )

            firestore_changed += 1

            continue

        # =================================================
        # DOKUMEN SUDAH ADA
        #
        # Jangan menganggapnya data baru.
        # updated_at tidak dipakai untuk menentukan
        # apakah data berubah.
        # =================================================

        old = existing_doc.to_dict() or {}

        data_changed = (

            str(
                old.get("tanggal", "")
            ).strip()
            != tanggal

            or

            str(
                old.get("periode", "")
            ).strip()
            != periode

            or

            str(
                old.get("nomor", "")
            ).strip()
            != nomor

            or

            old.get("source_url")
            != row.get("source_url")
        )

        if not data_changed:

            continue

        # =================================================
        # DATA MEMANG BERUBAH
        # =================================================

        new_data[
            "updated_at"
        ] = firestore.SERVER_TIMESTAMP

        doc_ref.set(
            new_data,
            merge=True
        )

        firestore_changed += 1

    # =====================================================
    # SINKRON KE DTB1
    #
    # Tetap dilakukan walaupun Firestore tidak bertambah,
    # karena DTB1 bisa saja perlu dibersihkan/disinkronkan.
    # =====================================================

    sync_to_dtb1(
        rows
    )

    # =====================================================
    # PENTING:
    #
    # Yang dikembalikan ke app.py adalah jumlah perubahan
    # NYATA di Firestore.
    #
    # Bukan jumlah perubahan DTB1.
    # =====================================================

    return firestore_changed


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

    offset = (
        page - 1
    ) * per_page

    docs = (
        db.collection("history")
        .stream()
    )

    rows = []

    for doc in docs:

        data = doc.to_dict()

        rows.append({

            "tanggal":
                data.get("tanggal"),

            "periode":
                data.get("periode"),

            "nomor":
                data.get("nomor"),

            "source_url":
                data.get("source_url")

        })

    # =====================================================
    # URUTKAN DATA TERBARU DI ATAS
    # =====================================================

    rows.sort(

        key=lambda x: (

            x.get("tanggal")
            or "",

            x.get("periode")
            or ""

        ),

        reverse=True
    )

    return rows[
        offset:
        offset + per_page
    ]


# =========================================================
# HITUNG DATA
# =========================================================

def count_rows():

    db = get_firestore_db()

    docs = (
        db.collection("history")
        .stream()
    )

    return sum(
        1
        for _ in docs
    )
