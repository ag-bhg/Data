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

from database import get_db_connection, JADWAL_PASARAN, get_rows_as_of, get_kode_pasaran_countdown

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
    {kode, nama, detik_menuju_hasil}, DIURUTKAN dari yang PALING
    CEPAT result dulu (bukan alfabetis lagi) -- detik_menuju_hasil
    dari get_kode_pasaran_countdown().

    detik_menuju_hasil bisa None kalau somehow tidak ketemu jadwal
    berikutnya dalam 8 hari ke depan (seharusnya tidak terjadi) --
    entri begini ditaruh paling akhir, bukan bikin error.
    """
    countdown = get_kode_pasaran_countdown()

    daftar = [
        {
            "kode": kode,
            "nama": info["nama"],
            "detik_menuju_hasil": countdown.get(kode),
        }
        for kode, info in JADWAL_PASARAN.items()
    ]

    daftar.sort(key=lambda p: (p["detik_menuju_hasil"] is None, p["detik_menuju_hasil"]))

    return daftar


# =========================================================
# AMBIL HASIL SEMUA PASARAN DI 1 ZONA, 1 TANGGAL
# =========================================================

def _ambil_hasil_tanggal(conn, tanggal, kode_list):
    """{kode_pasaran: nomor} -- 1 hasil per kode pada tanggal itu.
    Kalau kode punya >1 draw/hari (mis. KK), diambil yang sort_key
    paling besar (hasil paling akhir hari itu).

    Menerima koneksi yang SUDAH DIBUKA (dari bangun_tabel) --
    tidak buka/tutup koneksi sendiri, supaya 1 request tabel cuma
    pakai 1 koneksi ke Postgres, bukan 1 koneksi baru tiap baris.
    """
    if not kode_list:
        return {}

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT ON (kode_pasaran) kode_pasaran, nomor
            FROM history
            WHERE tanggal = %s AND kode_pasaran = ANY(%s)
            ORDER BY kode_pasaran, sort_key DESC;
            """,
            (tanggal, kode_list),
        )
        rows = cur.fetchall()
    return {r["kode_pasaran"]: r["nomor"] for r in rows}


def hitung_angka_terbaik(conn, tanggal, opsi, zona_label, n_digit):
    """
    None kalau belum memenuhi syarat (< MIN_PASARAN_UNTUK_HITUNG pasaran
    sudah keluar di zona+tanggal itu). Kalau memenuhi, string N digit
    (0-9) terurut dari paling sering muncul.
    """
    zona_pasaran = kelompokkan_pasaran(opsi).get(zona_label, [])
    kode_list = [p["kode"] for p in zona_pasaran]

    hasil_map = _ambil_hasil_tanggal(conn, tanggal, kode_list)
    if len(hasil_map) < MIN_PASARAN_UNTUK_HITUNG:
        return None

    counter = Counter()
    for nomor in hasil_map.values():
        for digit in str(nomor).strip().zfill(4)[-4:]:
            counter[digit] += 1

    ranking = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
    top = [digit for digit, _ in ranking[:n_digit]]
    return "".join(top)


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

    PERFORMA: 1 koneksi Postgres dibuka di sini dan dipakai
    bersama untuk SELURUH baris + SELURUH opsi (lewat
    hitung_angka_terbaik -> _ambil_hasil_tanggal). Sebelumnya tiap
    baris x tiap opsi buka koneksi baru sendiri-sendiri -- untuk
    30 baris x 3 opsi itu ~90 koneksi terpisah ke Neon dalam 1
    request, cukup lambat untuk bikin request timeout (makanya
    tabel nyangkut di "Memuat..." terus, bukan error yang
    kelihatan).
    """
    n_digit = max(4, min(int(n_digit), 9))
    jumlah_baris = max(1, min(int(jumlah_baris), 100))

    rows = get_rows_as_of(kode_acuan, jumlah_baris, tanggal_acuan)  # terbaru dulu

    conn = get_db_connection()
    try:
        tabel = []
        for r in rows:
            tanggal = r["tanggal"]
            baris = {
                "tanggal": tanggal,
                "periode": r["periode"],
                "nomor": r["nomor"],
                "opsi1": hitung_angka_terbaik(conn, tanggal, 1, zona_opsi1, n_digit) if zona_opsi1 else None,
                "opsi2": hitung_angka_terbaik(conn, tanggal, 2, zona_opsi2, n_digit) if zona_opsi2 else None,
                "opsi3": hitung_angka_terbaik(conn, tanggal, 3, zona_opsi3, n_digit) if zona_opsi3 else None,
            }
            tabel.append(baris)
    finally:
        conn.close()

    return tabel
