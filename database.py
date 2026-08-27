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
#
# 25-08-2026 KK-1209 6315
# 24-08-2026 KK-1208 3278
#
# Hasil:
# tanggal, periode, nomor
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
#
#   1
#     name: KK
#     data: ...
# =========================================================

def find_dtb1_node_in_data(saved_data, main_id):
    if not saved_data:
        return None, None

    # savedData berupa list
    if isinstance(saved_data, list):

        for index, item in enumerate(saved_data):

            if not isinstance(item, dict):
                continue

            name = str(
                item.get("name", "")
            ).strip()

            if name == main_id:
                return index, item

        return None, None

    # savedData berupa dictionary
    if isinstance(saved_data, dict):

        for key, item in saved_data.items():

            if not isinstance(item, dict):
                continue

            name = str(
                item.get("name", "")
            ).strip()

            if name == main_id:
                return key, item

    return None, None


# =========================================================
# Cari index baru tanpa merusak struktur savedData
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
#
# Menggunakan transaction agar:
# - dua proses bersamaan tidak saling menimpa
# - duplikat tidak masuk
# - duplikat lama dapat dibersihkan
# - struktur savedData lama tetap dipertahankan
# =========================================================

def sync_to_dtb1(rows):

    root_ref = get_rtdb()
    saved_ref = root_ref.child("savedData")

    changed = 0
    skipped = 0
    created = 0

    # =====================================================
    # DEDUPLIKASI DATA YANG DATANG DARI DTB2
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

    rows = list(unique_rows.values())

    if not rows:
        return {
            "changed": 0,
            "created": 0,
            "skipped": 0
        }

    # =====================================================
    # TRANSACTION
    #
    # Firebase akan membaca data terbaru setiap kali
    # transaction perlu diulang.
    # =====================================================

    def transaction_update(saved_data):

        nonlocal changed
        nonlocal skipped
        nonlocal created

        if saved_data is None:
            saved_data = []

        # Pastikan tipe tetap sama.
        if not isinstance(saved_data, (list, dict)):
            return saved_data

        # =================================================
        # PROSES SEMUA ROW DI DALAM SATU TRANSACTION
        # =================================================

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

            main_id = get_main_id(periode)

            new_entry = make_entry(row)

            # =============================================
            # CARI ID UTAMA
            # =============================================

            node_key, existing = find_dtb1_node_in_data(
                saved_data,
                main_id
            )

            # =============================================
            # ID BELUM ADA
            # =============================================

            if node_key is None:

                new_item = {
                    "data": new_entry,
                    "name": main_id,
                    "savedAt": current_saved_at()
                }

                # savedData berupa LIST
                if isinstance(saved_data, list):

                    saved_data.append(new_item)

                # savedData berupa DICTIONARY
                elif isinstance(saved_data, dict):

                    next_index = get_next_saved_data_index(
                        saved_data
                    )

                    saved_data[str(next_index)] = new_item

                created += 1
                changed += 1

                continue

            # =============================================
            # ID SUDAH ADA
            # =============================================

            old_data = str(
                existing.get("data", "")
            ).strip()

            old_entries = parse_data_entries(
                old_data
            )

            # =============================================
            # BERSIHKAN DUPLIKAT LAMA
            # =============================================

            unique_entries = []
            seen = set()

            for (
                old_tanggal,
                old_periode,
                old_nomor
            ) in old_entries:

                entry_key = (
                    old_tanggal,
                    old_periode,
                    old_nomor
                )

                if entry_key in seen:
                    continue

                seen.add(entry_key)

                unique_entries.append(
                    f"{old_tanggal} "
                    f"{old_periode} "
                    f"{old_nomor}"
                )

            # =============================================
            # CEK DATA BARU
            # =============================================

            new_key = (
                tanggal,
                periode,
                nomor
            )

            if new_key not in seen:

                # Data baru selalu berada di depan
                unique_entries.insert(
                    0,
                    new_entry
                )

                seen.add(new_key)

            # =============================================
            # SUSUN KEMBALI
            # =============================================

            new_data = " ".join(
                unique_entries
            )

            # =============================================
            # UPDATE HANYA JIKA ADA PERUBAHAN
            # =============================================

            if new_data == old_data:

                skipped += 1
                continue

            # =============================================
            # PERTAHANKAN FIELD LAIN
            # =============================================

            existing["data"] = new_data
            existing["name"] = main_id
            existing["savedAt"] = current_saved_at()

            # =============================================
            # SIMPAN KEMBALI KE NODE YANG SAMA
            # =============================================

            if isinstance(saved_data, list):

                saved_data[node_key] = existing

            elif isinstance(saved_data, dict):

                saved_data[str(node_key)] = existing

            changed += 1

        return saved_data

    # =====================================================
    # JALANKAN TRANSACTION
    # =====================================================

    saved_ref.transaction(
        transaction_update
    )

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

    # =====================================================
    # 1. SIMPAN KE DTB2
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

        data = {
            "tanggal": tanggal,
            "periode": periode,
            "nomor": nomor,
            "source_url": row.get("source_url"),
            "updated_at": firestore.SERVER_TIMESTAMP
        }

        db.collection(
            "history"
        ).document(
            doc_id
        ).set(
            data,
            merge=True
        )

        changed += 1

    # =====================================================
    # 2. SINKRON DTB2 → DTB1
    # =====================================================

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

    offset = (
        page - 1
    ) * per_page

    docs = db.collection(
        "history"
    ).stream()

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

    return rows[
        offset:
        offset + per_page
    ]


# =========================================================
# HITUNG DATA
# =========================================================

def count_rows():

    db = get_firestore_db()

    docs = db.collection(
        "history"
    ).stream()

    return sum(
        1 for _ in docs
    )
