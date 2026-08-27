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

def sync_history():
    from database import upsert_rows

    html, final_url = fetch(SOURCE_URL)
    all_rows = parse_table(html, final_url)

    # Ambil seluruh halaman pagination yang tersedia.
    page_links = find_page_links(html, final_url)
    seen_urls = {final_url}

    for page_no, page_url in page_links:
        if page_url in seen_urls:
            continue

        seen_urls.add(page_url)
        page_html, page_final_url = fetch(page_url)
        page_rows = parse_table(page_html, page_final_url)

        if not page_rows:
            continue

        all_rows.extend(page_rows)

    # Hilangkan duplikat dalam satu batch.
    unique = {(r["tanggal"], r["periode"], r["nomor"]): r for r in all_rows}
    return upsert_rows(list(unique.values()))
