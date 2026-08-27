import os
import json
import firebase_admin
from firebase_admin import credentials
from firebase_admin import db as realtime_db


APP_NAME = "dtb1"


def get_rtdb():

    try:
        app = firebase_admin.get_app(APP_NAME)

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
            name=APP_NAME
        )

    return realtime_db.reference(
        "savedData",
        app=app
    )


def main():

    print("=" * 60)
    print("PEMERIKSAAN DTB1 - READ ONLY")
    print("=" * 60)

    ref = get_rtdb()

    # HANYA MEMBACA
    saved_data = ref.get()

    if not saved_data:

        print()
        print("savedData kosong.")
        return

    print()
    print(
        "Tipe data:",
        type(saved_data).__name__
    )

    if isinstance(saved_data, dict):

        print(
            "Jumlah node:",
            len(saved_data)
        )

        print()
        print("DAFTAR NODE:")

        for key, item in saved_data.items():

            if not isinstance(item, dict):
                print(
                    f"[{key}] bukan object"
                )
                continue

            name = item.get(
                "name",
                ""
            )

            data = item.get(
                "data",
                ""
            )

            print()
            print(
                f"INDEX : {key}"
            )

            print(
                f"NAME  : {name}"
            )

            print(
                f"DATA  : {str(data)[:300]}"
            )

            extra_fields = [
                k
                for k in item.keys()
                if k not in (
                    "name",
                    "data",
                    "savedAt"
                )
            ]

            print(
                "FIELD LAIN:",
                extra_fields
            )

    elif isinstance(saved_data, list):

        print(
            "Jumlah node:",
            len(saved_data)
        )

        print()
        print("DAFTAR NODE:")

        for index, item in enumerate(
            saved_data
        ):

            if not isinstance(item, dict):
                print(
                    f"[{index}] bukan object"
                )
                continue

            name = item.get(
                "name",
                ""
            )

            data = item.get(
                "data",
                ""
            )

            print()
            print(
                f"INDEX : {index}"
            )

            print(
                f"NAME  : {name}"
            )

            print(
                f"DATA  : {str(data)[:300]}"
            )

            extra_fields = [
                k
                for k in item.keys()
                if k not in (
                    "name",
                    "data",
                    "savedAt"
                )
            ]

            print(
                "FIELD LAIN:",
                extra_fields
            )

    else:

        print(
            "Format savedData tidak dikenali."
        )


    print()
    print("=" * 60)
    print("PEMERIKSAAN SELESAI")
    print("TIDAK ADA DATA YANG DIUBAH.")
    print("=" * 60)


if __name__ == "__main__":
    main()
