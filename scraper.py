import re
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

SOURCE_URL = "https://dana100nl.com/draw-history"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; LocalDemoScraper/1.0)"
}

def fetch(url):
    response = requests.get(url, headers=HEADERS, timeout=20)
    response.raise_for_status()
    return response.text, response.url

def parse_table(html, source_url):
    soup = BeautifulSoup(html, "html.parser")
    rows = []

    # Cari tabel yang memiliki header Tanggal, Periode, Nomor.
    for table in soup.find_all("table"):
        headers = [x.get_text(" ", strip=True).lower() for x in table.find_all("th")]
        if not {"tanggal", "periode", "nomor"}.issubset(set(headers)):
            continue

        for tr in table.find_all("tr")[1:]:
            cells = [x.get_text(" ", strip=True) for x in tr.find_all(["td", "th"])]
            if len(cells) < 3:
                continue
            tanggal, periode, nomor = cells[:3]
            if re.fullmatch(r"\d{2}-\d{2}-\d{4}", tanggal) and re.fullmatch(r"\d{4}", nomor):
                rows.append({
                    "tanggal": tanggal,
                    "periode": periode,
                    "nomor": nomor,
                    "source_url": source_url
                })
        if rows:
            return rows

    # Fallback jika markup situs berubah: cari baris berdasarkan pola tanggal + 4 digit.
    for tr in soup.find_all("tr"):
        cells = [x.get_text(" ", strip=True) for x in tr.find_all(["td", "th"])]
        if len(cells) >= 3 and re.fullmatch(r"\d{2}-\d{2}-\d{4}", cells[0]) and re.fullmatch(r"\d{4}", cells[2]):
            rows.append({
                "tanggal": cells[0],
                "periode": cells[1],
                "nomor": cells[2],
                "source_url": source_url
            })
    return rows

def find_page_links(html, current_url):
    soup = BeautifulSoup(html, "html.parser")
    links = []
    for a in soup.find_all("a", href=True):
        text = a.get_text(" ", strip=True)
        href = urljoin(current_url, a["href"])
        if text.isdigit() and 1 <= int(text) <= 10000:
            links.append((int(text), href))
    return sorted(set(links))

def sync_history(start_page=1, hard_cap_pages=40, max_history_pages=300, max_new_rows=20, force_full_write=False):
    from database import (
        upsert_rows,
        get_pasaran_progress,
        update_pasaran_progress,
        extract_kode_pasaran,
        extract_urutan_pasaran,
        compute_group_sort_key,
    )

    # =====================================================
    # LOGIKA "CATCH-UP" (mengejar), bukan lagi batch tetap.
    #
    # Cek dulu progres TERBARU yang sudah tersimpan (1 read utk
    # semua kode pasaran, lihat get_pasaran_progress), lalu scrape
    # halaman 1, 2, 3, dst sampai ketemu halaman yang isinya SEMUA
    # sudah pernah tersimpan sebelumnya -> baru berhenti. Jadi
    # seberapa pun lama absen, tetap otomatis "mengejar" tanpa ada
    # yang bolong.
    #
    # hard_cap_pages membatasi jumlah halaman per SEKALI jalan,
    # supaya tidak timeout di serverless function. max_new_rows
    # membatasi jumlah DATA BARU per sekali jalan (default 20,
    # sync jalan tiap 5 menit lewat GitHub Actions) -- sisa gap
    # otomatis kekejar di jalan berikutnya (aman diulang, dedup
    # otomatis lewat doc_id).
    #
    # =====================================================
    # PERBAIKAN (sebelumnya BUG): dulu "sudah pernah tersimpan"
    # dicek pakai SATU sort_key global gabungan semua kode
    # pasaran (get_max_sort_key), yang isinya tanggal + TEKS
    # periode mentah. Karena perbandingannya string, nama kode
    # pasaran ikut menentukan "besar-kecil" walau itu tidak ada
    # hubungannya dengan waktu -- "KK-1223" dianggap "lebih
    # kecil" dari "YSLM-612" cuma karena K < Y secara alfabet.
    # Begitu satu kode berhuruf awal besar (YSLM, WDM, VIRD, dst)
    # pernah tersimpan di tanggal itu, kode-kode berhuruf awal
    # lebih kecil (KK, CRD, BLRS, DELD, dst) jadi PERMANEN
    # dianggap "sudah lama" dan tidak pernah ditulis lagi, walau
    # draw barunya belum pernah tersimpan sama sekali (persis
    # gejala "KK macet" yang teramati di sumber vs sistem sendiri).
    #
    # SEKARANG: progres dilacak PER KODE PASARAN sendiri-sendiri
    # (get_pasaran_progress/update_pasaran_progress di
    # database.py), dibandingkan pakai nomor urut asli
    # (compute_group_sort_key), bukan nama kode. Satu pasaran
    # tidak lagi bisa memblokir pasaran lain.
    # =====================================================

    if start_page < 1:
        start_page = 1

    known_progress = get_pasaran_progress()

    def _is_new(row):
        urutan = extract_urutan_pasaran(row["periode"])
        if urutan is None:
            # Format periode tidak bisa dipecah jadi (kode, urutan) ->
            # jangan sok tahu, anggap baru. Aman: doc_id Firestore
            # deterministik (tanggal_periode_nomor), jadi kalau
            # ternyata memang sudah ada, cuma ditimpa ulang dengan
            # isi identik, bukan bikin duplikat.
            return True
        kode = extract_kode_pasaran(row["periode"])
        group_key = compute_group_sort_key(row["tanggal"], urutan)
        last_known = known_progress.get(kode)
        return last_known is None or group_key > last_known

    all_rows = []
    page_no = start_page
    pages_scanned = 0
    new_row_count = 0
    reached_end_of_site = False
    matched_known_data = False
    hit_new_row_cap = False

    while pages_scanned < hard_cap_pages and page_no <= max_history_pages:
        if page_no == 1:
            page_url = SOURCE_URL
        else:
            page_url = f"{SOURCE_URL}?page={page_no}"

        try:
            html, final_url = fetch(page_url)
        except requests.RequestException:
            # Jangan memaksakan halaman berikutnya jika sumber gagal diakses.
            break

        page_rows = parse_table(html, final_url)

        # Tidak ada data berarti pagination sudah mencapai akhir data
        # di situs sumber (bukan cuma akhir batch kita).
        if not page_rows:
            reached_end_of_site = True
            break

        all_rows.extend(page_rows)
        pages_scanned += 1
        page_no += 1

        page_new_flags = [_is_new(r) for r in page_rows]
        page_new_count = sum(page_new_flags)

        if force_full_write:
            # Mode paksa: tetap hitung "data baru" buat laporan, tapi
            # JANGAN berhenti gara-gara progres -- baca terus sampai
            # hard_cap_pages habis atau situs sumber habis.
            new_row_count += page_new_count
            continue

        new_row_count += page_new_count

        if not any(page_new_flags):
            # Semua baris di halaman ini sudah pernah tersimpan
            # sebelumnya (per pasarannya masing-masing) -> sudah
            # mengejar sampai data lama, berhenti.
            matched_known_data = True
            break

        if new_row_count >= max_new_rows:
            # Sudah dapat cukup data baru untuk sekali jalan ini.
            # Sisa gap (kalau ada) otomatis kekejar di jalan
            # berikutnya 5 menit lagi.
            hit_new_row_cap = True
            break

    # Hilangkan duplikat dalam batch berdasarkan identitas data.
    unique = {
        (r["tanggal"], r["periode"], r["nomor"]): r
        for r in all_rows
    }

    # Buang baris yang TERNYATA sudah pernah tersimpan (per
    # progres pasarannya masing-masing -- bukan patokan global lagi).
    # force_full_write=True sengaja melewati filter ini (dipakai
    # tombol update manual): semua baris di window ditulis apa
    # adanya, aman dari duplikat karena doc_id Firestore
    # deterministik.
    if not force_full_write:
        unique = {key: r for key, r in unique.items() if _is_new(r)}

    changed = upsert_rows(list(unique.values()))

    # Majukan progres per-pasaran berdasarkan SEMUA baris yang
    # terbaca batch ini (all_rows, bukan cuma yang lolos filter),
    # supaya "garis depan" tiap pasaran tetap representatif.
    update_pasaran_progress(all_rows, known_progress)

    caught_up = (matched_known_data or reached_end_of_site) and not hit_new_row_cap

    return {
        "changed": changed,
        "pages_scanned": pages_scanned,
        "new_rows": new_row_count,
        "caught_up": caught_up,
    }
