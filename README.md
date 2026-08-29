# Draw History Demo Lokal

[Demo ini mengambil tiga field dari halaman sumber: **Tanggal, Periode, Nomor**, lalu menyimpannya ke SQLite dan menampilkannya melalui Flask.](https://data-httxsi4pu-ag-4d25.vercel.app/)

## 1. Install

Python 3.10+ disarankan.

```bash
python -m venv .venv
```

Windows:
```bash
.venv\Scripts\activate
```

Linux/macOS:
```bash
source .venv/bin/activate
```

```bash
pip install -r requirements.txt
```

## 2. Jalankan

```bash
python app.py
```

Buka:

http://127.0.0.1:5000

Lalu tekan **Update Data**.

## 3. Catatan scraper

Scraper sengaja dibatasi `max_pages=10` agar demo tidak langsung meminta ribuan halaman.

Jika struktur HTML sumber berubah, bagian yang perlu disesuaikan ada di `scraper.py`, terutama `parse_table()` dan `find_page_links()`.

Gunakan hanya untuk data yang memang boleh Anda ambil dan tampilkan kembali. Untuk penggunaan publik, periksa ketentuan penggunaan situs sumber, hak cipta, dan aturan terkait data sebelum melakukan sinkronisasi otomatis.
