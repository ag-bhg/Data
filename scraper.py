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

def sync_history(start_page=1, hard_cap_pages=40, max_history_pages=300):
    from database import upsert_rows, get_max_sort_key, compute_sort_key

    # =====================================================
    # LOGIKA "CATCH-UP" (mengejar), bukan lagi batch tetap.
    #
    # Sebelumnya: selalu baca halaman 1-20 saja tiap sync, apa
    # pun kondisinya. Kalau tidak sync beberapa hari, data baru
    # dari BANYAK pasaran gabungan bisa menggeser data lama itu
    # sampai melebihi 20 halaman -> ada bagian riwayat yang
    # PERMANEN terlewat (bikin urutan Periode terlihat
    # melompat-lompat / acak).
    #
    # Sekarang: cek dulu sort_key TERBARU yang sudah tersimpan
    # (1 read saja), lalu scrape halaman 1, 2, 3, dst sampai
    # ketemu halaman yang isinya SEMUA sudah pernah tersimpan
    # sebelumnya -> baru berhenti. Jadi seberapa pun lama absen,
    # tetap otomatis "mengejar" tanpa ada yang bolong.
    #
    # hard_cap_pages membatasi jumlah halaman per SEKALI klik,
    # supaya tidak timeout di serverless function. Kalau gap-nya
    # lebih dalam dari itu, hasilnya "caught_up": False -> user
    # tinggal klik "Update Data" sekali lagi untuk lanjut mengejar
    # sisanya (aman diulang, dedup otomatis lewat doc_id).
    # =====================================================

    if start_page < 1:
        start_page = 1

    known_max_sort_key = get_max_sort_key()

    all_rows = []
    page_no = start_page
    pages_scanned = 0
    reached_end_of_site = False
    matched_known_data = False

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

        if known_max_sort_key is not None:
            page_sort_keys = [
                compute_sort_key(r["tanggal"], r["periode"])
                for r in page_rows
            ]
            if all(k <= known_max_sort_key for k in page_sort_keys):
                # Semua baris di halaman ini sudah pernah tersimpan
                # sebelumnya -> sudah mengejar sampai data lama, berhenti.
                matched_known_data = True
                break

    # Hilangkan duplikat dalam batch berdasarkan identitas data.
    unique = {
        (r["tanggal"], r["periode"], r["nomor"]): r
        for r in all_rows
    }

    changed = upsert_rows(list(unique.values()))

    caught_up = matched_known_data or reached_end_of_site

    return {
        "changed": changed,
        "pages_scanned": pages_scanned,
        "caught_up": caught_up,
    }
