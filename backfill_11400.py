"""
backfill_11400.py

Script MANDIRI (terpisah dari sistem utama) untuk mengejar riwayat
lama sampai target ~11.400 data, TANPA mengubah logika sync_history()
/ app.py yang sudah berjalan.

Strategi (sesuai arahan):
- Ambil 40 halaman -> tulis ke Firestore -> jeda 15 detik -> lanjut
  40 halaman berikutnya -> ulangi sampai target tercapai atau sumber
  data habis.
- Aman dijalankan kapan pun, bahkan berbarengan dengan sync_history()
  yang biasa, karena menulis ke collection Firestore yang SAMA lewat
  doc_id deterministik (tanggal_periode_nomor) + merge=True -> tidak
  mungkin menghasilkan duplikat walau berjalan bersamaan.
- TIDAK mengimpor / memanggil sync_history() sama sekali. Hanya pakai
  fetch() dan parse_table() dari scraper.py (baca halaman & baca
  tabel), lalu upsert_rows() dan count_rows() dari database.py
  (tulis ke Firestore & hitung total) -- keduanya fungsi yang SUDAH
  ADA dan tidak diubah.

Cara pakai:
    - Jalankan LOKAL atau lewat shell terpisah (Render Shell, dsb),
      BUKAN lewat web route, supaya tidak kena batas waktu HTTP.
    - Environment variable FIREBASE_CREDENTIALS harus sudah diset
      sama seperti saat menjalankan app.py biasa.

    python backfill_11400.py
"""

import time

from scraper import fetch, parse_table
from database import upsert_rows, count_rows

TARGET_TOTAL = 11400
PAGES_PER_BATCH = 40
JEDA_ANTAR_BATCH = 15       # detik, sesuai arahan
JEDA_ANTAR_HALAMAN = 0.4    # detik, supaya sopan ke situs sumber
SOURCE_URL = "https://dana100nl.com/draw-history"


def scrape_page_range(start_page, end_page):
    """Ambil baris dari halaman start_page..end_page (inklusif)."""
    rows = []
    reached_end = False

    for page_no in range(start_page, end_page + 1):
        page_url = (
            SOURCE_URL if page_no == 1
            else f"{SOURCE_URL}?page={page_no}"
        )

        try:
            html, final_url = fetch(page_url)
        except Exception as exc:
            print(f"  Halaman {page_no}: gagal diambil ({exc}), berhenti di batch ini.")
            break

        page_rows = parse_table(html, final_url)

        if not page_rows:
            print(f"  Halaman {page_no}: kosong -> sudah mentok akhir data sumber.")
            reached_end = True
            break

        rows.extend(page_rows)
        print(f"  Halaman {page_no}: {len(page_rows)} baris")

        time.sleep(JEDA_ANTAR_HALAMAN)

    return rows, reached_end


def main():
    start_before = count_rows()
    print(f"Jumlah data saat ini di Firestore: {start_before}")
    print(f"Target total: {TARGET_TOTAL}\n")

    current_page = 1
    batch_no = 1

    while True:
        total_now = count_rows()

        if total_now >= TARGET_TOTAL:
            print(f"\nTarget {TARGET_TOTAL} sudah tercapai (sekarang: {total_now}). Selesai.")
            break

        end_page = current_page + PAGES_PER_BATCH - 1
        print(f"--- Batch {batch_no}: halaman {current_page}-{end_page} ---")

        rows, reached_end = scrape_page_range(current_page, end_page)

        if rows:
            unique = {
                (r["tanggal"], r["periode"], r["nomor"]): r
                for r in rows
            }
            written = upsert_rows(list(unique.values()))
            print(f"  -> {written} baris ditulis/ditimpa ke Firestore.")

        total_now = count_rows()
        print(f"  Total data sekarang: {total_now} / {TARGET_TOTAL}")

        if reached_end:
            print("\nSudah sampai akhir data di situs sumber, tidak ada lagi yang bisa diambil.")
            break

        current_page = end_page + 1
        batch_no += 1

        print(f"  Jeda {JEDA_ANTAR_BATCH} detik sebelum batch berikutnya...\n")
        time.sleep(JEDA_ANTAR_BATCH)

    final_total = count_rows()
    print(f"\nSelesai. Total data di Firestore sekarang: {final_total}")
    print(f"Data baru yang bertambah dari sesi ini: {final_total - start_before}")


if __name__ == "__main__":
    main()
