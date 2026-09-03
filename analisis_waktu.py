"""
Analisis Angka per Zona Waktu (v2 - "colok bebas per zona")
=============================================================

Untuk 1 pasaran acuan (Ddwn1), tampilkan 12 draw terakhirnya. Di tiap
baris (1 tanggal draw), hitung "angka terbaik" (digit 0-9 yang paling
sering hadir, digabung tanpa lihat posisi) dari SEMUA pasaran lain
yang berada di zona waktu yang sama PADA TANGGAL ITU JUGA -- dihitung
terpisah untuk 3 skema pembagian zona (Opsi1=4 zona, Opsi2=3 zona,
Opsi3=2 zona), masing-masing zonanya dipilih sendiri lewat dropdown.

Syarat minimal 7 pasaran sudah keluar hasilnya pada tanggal & zona itu
supaya dihitung -- kalau belum, selnya "?" (menunggu hasil / belum
memenuhi syarat pengujian).

Depends on: database.py (get_db_connection, JADWAL_PASARAN, get_rows)
"""

from collections import Counter
from datetime import datetime

from database import (
    get_db_connection,
    JADWAL_PASARAN,
    get_rows_as_of,
    get_kode_pasaran_countdown,
    WIB,
)

MIN_PASARAN_UNTUK_HITUNG = 7


# =========================================================
# DEFINISI ZONA PER OPSI
# =========================================================
ZONA_OPSI = {
    1: [
        ("Z1", "00:01", "06:00"),
        ("Z2", "06:01", "12:00"),
        ("Z3", "12:01", "18:00"),
        ("Z4", "18:01", "00:00"),
    ],
    2: [
        ("Z1", "00:01", "08:00"),
        ("Z2", "08:01", "16:00"),
        ("Z3", "16:01", "00:00"),
    ],
    3: [
        ("Z1", "00:01", "12:00"),
        ("Z2", "12:01", "00:00"),
    ],
}


def _menit(jam_str):
    jam, menit = map(int, jam_str.split(":"))
    return jam * 60 + menit


def _dalam_zona(menit_hasil, mulai_str, akhir_str):
    mulai = _menit(mulai_str)
    akhir = _menit(akhir_str)
    if akhir == 0:
        akhir = 24 * 60
    if menit_hasil == 0:
        menit_hasil = 24 * 60
    return mulai <= menit_hasil <= akhir


def get_zona_list(opsi):
    return ZONA_OPSI.get(opsi, ZONA_OPSI[1])


def kelompokkan_pasaran(opsi):
    """{label_zona: [{"kode","nama","jam_hasil"}]} untuk 1 opsi."""
    zona_def = get_zona_list(opsi)
    hasil = {label: [] for label, _, _ in zona_def}

    for kode, info in JADWAL_PASARAN.items():
        jam_hasil_list = [d[1] for d in info["draws"]]
        zona_cocok = set()
        for jam_hasil in jam_hasil_list:
            m = _menit(jam_hasil)
            for label, mulai, akhir in zona_def:
                if _dalam_zona(m, mulai, akhir):
                    zona_cocok.add(label)
        for label in zona_cocok:
            hasil[label].append({
                "kode": kode,
                "nama": info["nama"],
                "jam_hasil": ", ".join(jam_hasil_list),
            })

    for label in hasil:
        hasil[label].sort(key=lambda x: x["kode"])
    return hasil


def daftar_periode():
    """
    Semua pasaran yang punya jadwal, buat isi Ddwn1. Tiap entri:
    {kode, nama, jam_label}, DIURUTKAN dari yang PALING CEPAT
    result dulu (bukan alfabetis).

    jam_label itu STATIS (mis. "22:15" / "Besok 03:00") -- dihitung
    sekali di server tiap kali endpoint ini dipanggil, BUKAN
    countdown yang perlu di-refresh tiap detik di browser. Sengaja
    begini supaya dropdown-nya tidak "kedip" (browser di HP
    me-render ulang komponen <select> kalau isinya sering diubah
    lewat JS, apalagi kalau lagi dibuka) dan tidak ada proses
    background yang jalan terus-terusan di sisi klien.

    Entri yang somehow tidak ketemu jadwal berikutnya dalam 8 hari
    (seharusnya tidak terjadi) ditaruh paling akhir, bukan bikin
    error.
    """
    countdown = get_kode_pasaran_countdown()

    daftar = [
        {
            "kode": kode,
            "nama": info["nama"],
            "jam_label": countdown.get(kode, {}).get("jam_label"),
            "_detik": countdown.get(kode, {}).get("detik"),
        }
        for kode, info in JADWAL_PASARAN.items()
    ]

    daftar.sort(key=lambda p: (p["_detik"] is None, p["_detik"]))

    for p in daftar:
        del p["_detik"]  # cuma dipakai buat urutkan, tidak perlu dikirim ke frontend

    return daftar


# =========================================================
# AMBIL/HITUNG RANKING DIGIT, DIBATCH PER OPSI (bukan per tanggal)
# + DI-CACHE PERMANEN (kecuali tanggal hari ini)
#
# Hasil "digit terbanyak" untuk 1 kombinasi (tanggal, opsi, zona)
# TIDAK PERNAH BERUBAH lagi begitu tanggalnya sudah lewat -- jadi
# begitu pernah dihitung sekali, aman disimpan permanen dan dipakai
# lagi dari pasaran acuan MANAPUN (Ddwn1 cuma menentukan tanggal
# mana yang diminta, bukan isi hitungannya). Satu-satunya
# pengecualian: tanggal HARI INI, karena datanya bisa masih
# bertambah sepanjang hari itu -- tidak pernah di-cache.
# =========================================================

def _hari_ini_str():
    now = datetime.now(WIB) if WIB else datetime.now()
    return now.strftime("%d-%m-%Y")  # format sama dengan history.tanggal


def _hitung_ranking_dari_hasil(hasil_map):
    """
    String 10 digit (semua 0-9, bukan cuma yang muncul) terurut
    dari paling sering hadir -- None kalau < MIN_PASARAN_UNTUK_HITUNG
    pasaran punya hasil. Simpan SEMUA 10 digit (bukan cuma n_digit)
    supaya kalau Ddwn5 diganti, tinggal potong dari sini, tidak
    perlu hitung ulang dari nol.
    """
    if len(hasil_map) < MIN_PASARAN_UNTUK_HITUNG:
        return None

    counter = Counter({str(d): 0 for d in range(10)})
    for nomor in hasil_map.values():
        for digit in str(nomor).strip().zfill(4)[-4:]:
            counter[digit] += 1

    ranking = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
    return "".join(digit for digit, _ in ranking)


def _ambil_ranking_batch(conn, tanggal_list, opsi, zona_label):
    """
    {tanggal: urutan_digit_atau_None} untuk SEMUA tanggal di
    tanggal_list sekaligus, untuk 1 kombinasi (opsi, zona_label).

    Alur:
    1. Baca cache utk tanggal yg BUKAN hari ini -- biasanya kena
       semua tanggal kecuali hari berjalan begitu cache "panas".
    2. Sisanya (belum pernah ke-cache, atau memang hari ini)
       dihitung lewat SATU query gabungan ke "history" (bukan 1
       query per tanggal seperti sebelumnya).
    3. Hasil yg BUKAN hari ini disimpan ke cache buat dipakai
       lagi nanti -- dari pasaran acuan manapun, bukan cuma
       request ini.
    """
    if not tanggal_list:
        return {}

    hari_ini = _hari_ini_str()
    hasil = {}

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT tanggal, urutan_digit FROM analisis_zona_cache
            WHERE opsi = %s AND zona_label = %s AND tanggal = ANY(%s);
            """,
            (opsi, zona_label, tanggal_list),
        )
        for row in cur.fetchall():
            hasil[row["tanggal"]] = row["urutan_digit"]  # bisa None ("kurang data", tetap valid dari cache)

    tanggal_belum_ada = [t for t in tanggal_list if t not in hasil]

    if tanggal_belum_ada:
        zona_pasaran = kelompokkan_pasaran(opsi).get(zona_label, [])
        kode_list = [p["kode"] for p in zona_pasaran]

        peta_per_tanggal = {t: {} for t in tanggal_belum_ada}

        if kode_list:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT DISTINCT ON (tanggal, kode_pasaran)
                        tanggal, kode_pasaran, nomor
                    FROM history
                    WHERE tanggal = ANY(%s) AND kode_pasaran = ANY(%s)
                    ORDER BY tanggal, kode_pasaran, sort_key DESC;
                    """,
                    (tanggal_belum_ada, kode_list),
                )
                for row in cur.fetchall():
                    peta_per_tanggal[row["tanggal"]][row["kode_pasaran"]] = row["nomor"]

        baris_cache_baru = []
        for t in tanggal_belum_ada:
            urutan = _hitung_ranking_dari_hasil(peta_per_tanggal[t])
            hasil[t] = urutan
            if t != hari_ini:
                baris_cache_baru.append((t, opsi, zona_label, urutan))

        if baris_cache_baru:
            with conn.cursor() as cur:
                cur.executemany(
                    """
                    INSERT INTO analisis_zona_cache (tanggal, opsi, zona_label, urutan_digit)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (tanggal, opsi, zona_label) DO NOTHING;
                    """,
                    baris_cache_baru,
                )

    return hasil


# =========================================================
# TABEL UTAMA: 12 draw terakhir pasaran acuan + 3 kolom opsi
# =========================================================

def bangun_tabel(kode_acuan, zona_opsi1, zona_opsi2, zona_opsi3, n_digit,
                  jumlah_baris=12, tanggal_acuan=None):
    """
    tanggal_acuan: "YYYY-MM-DD" opsional -- sumber datanya (12 draw
    terakhir pasaran acuan) dihitung mundur dari tanggal ini.
    None/kosong berarti dihitung mundur dari data terbaru (hari
    ini), sama seperti perilaku sebelumnya.

    PERFORMA: sebelumnya tiap baris x tiap opsi query terpisah ke
    Postgres (12 baris x 3 opsi = 36 query per request). Sekarang
    dibatch PER OPSI -- 1 query gabungan yang mencakup SEMUA
    tanggal sekaligus (bukan 1 query per tanggal), ditambah cache
    permanen di analisis_zona_cache (lihat _ambil_ranking_batch)
    supaya tanggal yang sudah pernah dihitung dari pasaran acuan
    manapun tidak perlu dihitung ulang. Hasil: turun dari sampai 36
    query jadi paling banyak ~3 query per request (dan makin
    sedikit lagi begitu cache-nya "panas").
    """
    n_digit = max(4, min(int(n_digit), 9))
    jumlah_baris = max(1, min(int(jumlah_baris), 100))

    rows = get_rows_as_of(kode_acuan, jumlah_baris, tanggal_acuan)  # terbaru dulu
    tanggal_list = [r["tanggal"] for r in rows]

    conn = get_db_connection()
    try:
        peta_opsi = {}
        if zona_opsi1:
            peta_opsi[1] = _ambil_ranking_batch(conn, tanggal_list, 1, zona_opsi1)
        if zona_opsi2:
            peta_opsi[2] = _ambil_ranking_batch(conn, tanggal_list, 2, zona_opsi2)
        if zona_opsi3:
            peta_opsi[3] = _ambil_ranking_batch(conn, tanggal_list, 3, zona_opsi3)
        conn.commit()  # simpan baris cache baru yang barusan ditulis
    finally:
        conn.close()

    def _potong(urutan):
        return urutan[:n_digit] if urutan else None

    tabel = []
    for r in rows:
        t = r["tanggal"]
        tabel.append({
            "tanggal": t,
            "periode": r["periode"],
            "nomor": r["nomor"],
            "opsi1": _potong(peta_opsi.get(1, {}).get(t)) if zona_opsi1 else None,
            "opsi2": _potong(peta_opsi.get(2, {}).get(t)) if zona_opsi2 else None,
            "opsi3": _potong(peta_opsi.get(3, {}).get(t)) if zona_opsi3 else None,
        })

    return tabel
