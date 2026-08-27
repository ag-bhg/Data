def sync_to_dtb1(rows):
    root_ref = get_rtdb()
    saved_ref = root_ref.child("savedData")

    changed = 0
    skipped = 0
    created = 0

    # Hilangkan duplikat dari rows yang masuk
    # berdasarkan tanggal + periode + nomor
    unique_rows = {}

    for row in rows:
        tanggal = str(row["tanggal"]).strip()
        periode = str(row["periode"]).strip()
        nomor = str(row["nomor"]).strip()

        key = (tanggal, periode, nomor)

        if key not in unique_rows:
            unique_rows[key] = row

    rows = list(unique_rows.values())

    # Ambil savedData sekali
    saved_data = saved_ref.get()

    if not saved_data:
        saved_data = []

    # =====================================================
    # NORMALISASI savedData menjadi list
    # =====================================================

    if isinstance(saved_data, dict):
        ordered = []

        for key, item in saved_data.items():
            if isinstance(item, dict):
                ordered.append((key, item))

        ordered.sort(
            key=lambda x: int(x[0])
            if str(x[0]).isdigit()
            else 999999999
        )

        saved_data = [
            item for _, item in ordered
        ]

    elif not isinstance(saved_data, list):
        saved_data = []

    # =====================================================
    # PROSES SETIAP DATA BARU
    # =====================================================

    for row in rows:

        tanggal = str(row["tanggal"]).strip()
        periode = str(row["periode"]).strip()
        nomor = str(row["nomor"]).strip()

        main_id = get_main_id(periode)

        new_entry = make_entry(row)

        # =================================================
        # CARI ID DI savedData
        # =================================================

        node_index = None

        for index, item in enumerate(saved_data):

            if not isinstance(item, dict):
                continue

            name = str(
                item.get("name", "")
            ).strip()

            if name == main_id:
                node_index = index
                break

        # =================================================
        # ID BELUM ADA → BUAT BARU
        # =================================================

        if node_index is None:

            saved_data.append({
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

        item = saved_data[node_index]

        old_data = str(
            item.get("data", "")
        ).strip()

        # =================================================
        # PECAH DATA LAMA
        # =================================================

        old_entries = parse_data_entries(old_data)

        unique_entries = []
        seen = set()

        # -------------------------------------------------
        # Bersihkan duplikat lama
        # -------------------------------------------------

        for old_tanggal, old_periode, old_nomor in old_entries:

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
        # CEK DATA BARU
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

            seen.add(new_key)

        # =================================================
        # SUSUN KEMBALI DATA
        # =================================================

        new_data = " ".join(unique_entries)

        # =================================================
        # TIDAK ADA PERUBAHAN
        # =================================================

        if new_data == old_data:

            skipped += 1
            continue

        # =================================================
        # UPDATE
        # =================================================

        item["data"] = new_data
        item["name"] = main_id
        item["savedAt"] = current_saved_at()

        changed += 1

    # =====================================================
    # SIMPAN SELURUH savedData SEKALI
    # =====================================================

    if changed > 0:
        saved_ref.set(saved_data)

    return {
        "changed": changed,
        "created": created,
        "skipped": skipped
    }
