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
    # SIMPAN KE FIRESTORE / DTB2
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


        # =================================================
        # ID DOKUMEN
        # =================================================

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
        # CEK DATA YANG SUDAH ADA
        # =================================================

        existing_doc = doc_ref.get()


        new_data = {
            "tanggal": tanggal,
            "periode": periode,
            "nomor": nomor,
            "source_url": row.get(
                "source_url"
            )
        }



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


        old = (
            existing_doc.to_dict()
            or {}
        )


        data_changed = (

            str(
                old.get(
                    "tanggal",
                    ""
                )
            ).strip()
            != tanggal

            or

            str(
                old.get(
                    "periode",
                    ""
                )
            ).strip()
            != periode

            or

            str(
                old.get(
                    "nomor",
                    ""
                )
            ).strip()
            != nomor

            or

            old.get(
                "source_url"
            )
            != row.get(
                "source_url"
            )
        )

        if not data_changed:
            continue



        new_data[
            "updated_at"
        ] = firestore.SERVER_TIMESTAMP

        doc_ref.set(
            new_data,
            merge=True
        )

        firestore_changed += 1



    return firestore_changed


def init_db():

    get_firestore_db()


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
                data.get(
                    "tanggal"
                ),

            "periode":
                data.get(
                    "periode"
                ),

            "nomor":
                data.get(
                    "nomor"
                ),

            "source_url":
                data.get(
                    "source_url"
                )
        })



    def sort_key(row):

        tanggal = str(
            row.get(
                "tanggal"
            ) or ""
        ).strip()


        try:

            day, month, year = (
                tanggal.split("-")
            )

            tanggal_key = (
                year,
                month,
                day
            )

        except ValueError:

            tanggal_key = (
                "",
                "",
                ""
            )


        periode = str(
            row.get(
                "periode"
            ) or ""
        ).strip()


        return (
            tanggal_key,
            periode
        )


    rows.sort(
        key=sort_key,
        reverse=True
    )
    

    return rows[
        offset:
        offset + per_page
    ]
    
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
