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
    # hard_cap_pages membatasi jumlah halaman per SEKALI jalan,
    # supaya tidak timeout di serverless function.
    #
    # max_new_rows membatasi jumlah DATA BARU (bukan halaman)
    # yang diambil per sekali jalan, default 20. Karena sekarang
    # sync jalan otomatis tiap 5 menit (lihat GitHub Actions),
    # tidak perlu memaksa mengejar SEMUA gap dalam 1x jalan -
    # cukup ambil ~20 data terbaru per jalan, sisanya otomatis
    # kekejar di jalan-jalan berikutnya 5 menit kemudian. Ini
    # menghemat kuota baca/tulis Firestore & beban ke situs
    # sumber, dibanding dulu langsung menyapu 2000-3000 data
    # sekaligus tiap update.
    #
    # Kalau gap-nya lebih dalam dari max_new_rows atau
    # hard_cap_pages, hasilnya "caught_up": False -> jalan
    # berikutnya (5 menit lagi) otomatis lanjut mengejar sisanya
    # (aman diulang, dedup otomatis lewat doc_id).
    #
    # =====================================================
    # BUG KETAHUAN: sort_key = tanggal + TEKS periode mentah.
    # Koleksi "history" campur BANYAK kode pasaran (POL, YSLM,
    # PNM, OR1, HELS, RM, CRD, NYM, BLRS, dst) dalam tanggal yang
    # sama. Perbandingan sort_key di bawah ini murni STRING, jadi
    # "POL-612" dianggap "lebih kecil" dari "YSLM-612" cuma
    # karena P < Y secara alfabet -- padahal keduanya sama-sama
    # data tanggal yang sama, sama-sama valid, cuma beda pasaran.
    # Akibatnya begitu satu kode pasaran yang "besar" alfabetnya
    # (mis. YSLM) sudah pernah tersimpan, kode pasaran lain yang
    # "kecil" alfabetnya (POL, PNM, OR1, dst) di tanggal SAMA jadi
    # dikira "sudah lama" dan tidak pernah ditulis -- walau
    # sebenarnya belum pernah ada di database sama sekali.
    #
    # force_full_write=True (dipakai tombol update manual) untuk
    # menghindari ini: SEMUA baris yang terbaca di window
    # hard_cap_pages ditulis apa adanya, tanpa berhenti/filter
    # berdasarkan sort_key. Aman dari data kembar karena doc_id
    # Firestore = tanggal_periode_nomor (baca komentar di
    # database.py upsert_rows), jadi baris yang sudah ada ya
    # cuma ditimpa ulang dengan isi yang identik, tidak bikin
    # dokumen baru.
    # =====================================================

    if start_page < 1:
        start_page = 1

    known_max_sort_key = get_max_sort_key()

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

        if force_full_write:
            # Mode paksa: tetap hitung "data baru" buat laporan, tapi
            # JANGAN berhenti/skip gara-gara sort_key -- baca terus
            # sampai hard_cap_pages habis atau situs sumber habis.
            if known_max_sort_key is not None:
                page_sort_keys = [
                    compute_sort_key(r["tanggal"], r["periode"])
                    for r in page_rows
                ]
                new_row_count += sum(
                    1 for k in page_sort_keys if k > known_max_sort_key
                )
            else:
                new_row_count += len(page_rows)
            continue

        if known_max_sort_key is not None:
            page_sort_keys = [
                compute_sort_key(r["tanggal"], r["periode"])
                for r in page_rows
            ]
            new_row_count += sum(
                1 for k in page_sort_keys if k > known_max_sort_key
            )
            if all(k <= known_max_sort_key for k in page_sort_keys):
                # Semua baris di halaman ini sudah pernah tersimpan
                # sebelumnya -> sudah mengejar sampai data lama, berhenti.
                matched_known_data = True
                break
        else:
            # Koleksi masih kosong (belum pernah sync sama sekali) ->
            # semua baris yang terbaca dihitung sebagai "baru".
            new_row_count += len(page_rows)

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

    # =====================================================
    # PERBAIKAN: buang baris yang TERNYATA sudah pernah tersimpan
    # (sort_key-nya <= known_max_sort_key).
    #
    # Baris seperti ini bisa ikut terbawa dari halaman "batas"
    # (halaman yang isinya campuran data baru + data lama sekaligus).
    # Kalau ikut dikirim ke upsert_rows, dokumen lama itu ditulis
    # ulang (merge=True) dan field "updated_at"-nya ikut ter-refresh
    # ke waktu SEKARANG — padahal bukan data baru. Akibatnya:
    # 1) Data lama muncul seolah "baru disinkronkan" di
    #    get_rows_by_recency() (dipakai halaman utama "/"), jadi
    #    urutannya kacau.
    # 2) Boros kuota write Firestore untuk dokumen yang isinya
    #    sama persis dan tidak perlu ditulis ulang.
    #
    # force_full_write=True SENGAJA melewati filter ini -- lihat
    # catatan bug sort_key di atas. Konsekuensinya: dokumen lama
    # yang ikut kebaca ulang di window ini akan ter-refresh
    # updated_at-nya (bukan salah, cuma bikin dia numpang muncul
    # di atas daftar "terbaru" sebentar sampai data lebih baru
    # menggantikannya lagi).
    # =====================================================
    if known_max_sort_key is not None and not force_full_write:
        unique = {
            key: r for key, r in unique.items()
            if compute_sort_key(r["tanggal"], r["periode"]) > known_max_sort_key
        }

    changed = upsert_rows(list(unique.values()))

    caught_up = (matched_known_data or reached_end_of_site) and not hit_new_row_cap

    return {
        "changed": changed,
        "pages_scanned": pages_scanned,
        "new_rows": new_row_count,
        "caught_up": caught_up,
    }
