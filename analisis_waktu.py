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
    """
    {label_zona: [{"kode","nama","jam_hasil","urutan_draw"}]} untuk 1
    opsi.

    "urutan_draw": ke berapa (0=paling pagi) draw INI diantara semua
    draw pasaran yang sama di HARI YANG SAMA, diurutkan dari jam
    paling pagi. Untuk pasaran yang cuma 1x draw sehari (mayoritas),
    ini selalu 0. Untuk yang lebih dari 1x sehari (mis. "KK" jam
    17:00 & 23:30), draw 17:00 dapat urutan_draw=0 dan draw 23:30
    dapat urutan_draw=1 -- dipakai _ambil_ranking_batch() supaya
    tiap zona ambil NOMOR DARI DRAW YANG BENAR, bukan selalu draw
    terakhir hari itu (lihat catatan bug di _ambil_ranking_batch).
    """
    zona_def = get_zona_list(opsi)
    hasil = {label: [] for label, _, _ in zona_def}

    for kode, info in JADWAL_PASARAN.items():
        jam_hasil_list = [d[1] for d in info["draws"]]

        # urutan kronologis tiap index draw (0=paling pagi), dipakai
        # buat pasaran dgn >1 draw/hari spy urutan_draw konsisten
        # dgn urutan periode yg juga bertambah sepanjang waktu
        urutan_per_index = {
            idx_asli: urutan
            for urutan, idx_asli in enumerate(
                sorted(range(len(jam_hasil_list)), key=lambda i: _menit(jam_hasil_list[i]))
            )
        }

        zona_per_draw = {}  # index draw -> label zona (1 draw = 1 zona, zona tidak overlap)
        for idx, jam_hasil in enumerate(jam_hasil_list):
            m = _menit(jam_hasil)
            for label, mulai, akhir in zona_def:
                if _dalam_zona(m, mulai, akhir):
                    zona_per_draw[idx] = label
                    break

        for idx, label in zona_per_draw.items():
            hasil[label].append({
                "kode": kode,
                "nama": info["nama"],
                "jam_hasil": jam_hasil_list[idx],
                "urutan_draw": urutan_per_index[idx],
            })

    for label in hasil:
        hasil[label].sort(key=lambda x: x["kode"])
    return hasil


_HURUF_ZONA_PASARAN = {1: "a", 2: "b", 3: "c"}  # Opsi1->Za, Opsi2->Zb, Opsi3->Zc
_PREFIX_KE_OPSI = {v: k for k, v in _HURUF_ZONA_PASARAN.items()}


def daftar_zona_pasaran():
    """
    Semua kombinasi "Zona Pasaran" buat isi Ddwn1 (BARU -- dulu
    Ddwn1 isinya daftar pasaran satu-satu lewat daftar_periode(),
    sekarang isinya daftar ZONA: gabungan dari 3 skema, diberi nama
    Za1-4 (isi Zona Opsi1), Zb1-3 (isi Zona Opsi2), Zc1-2 (isi Zona
    Opsi3) -- biar 1 dropdown gabungan bisa langsung nunjuk ke 1
    (opsi, zona) tanpa perlu 2 langkah pilih.

    Tiap entri: {value, label, opsi} -- "opsi" dikirim ke frontend
    supaya bisa dikelompokkan pakai <optgroup> per Opsi1/2/3.
    """
    hasil = []
    for opsi, zona_def in ZONA_OPSI.items():
        huruf = _HURUF_ZONA_PASARAN[opsi]
        for label, mulai, akhir in zona_def:
            nomor = label[1:]  # "Z2" -> "2"
            value = f"Z{huruf}{nomor}"
            hasil.append({
                "value": value,
                "label": f"{value} ({mulai}-{akhir})",
                "opsi": opsi,
            })
    return hasil


def decode_zona_pasaran(kode_zona_pasaran):
    """
    "Za2" -> (1, "Z2"); "Zb1" -> (2, "Z1"); "Zc2" -> (3, "Z2").
    None kalau formatnya tidak dikenali (mis. dropdown belum
    dipilih / value nyasar).
    """
    kode_zona_pasaran = (kode_zona_pasaran or "").strip()
    if len(kode_zona_pasaran) < 3 or kode_zona_pasaran[0] != "Z":
        return None

    opsi = _PREFIX_KE_OPSI.get(kode_zona_pasaran[1].lower())
    nomor = kode_zona_pasaran[2:]
    if opsi is None or not nomor.isdigit():
        return None

    return opsi, f"Z{nomor}"


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

    1 pasaran = 1 suara per digit, WALAU digit itu ganda/triple/
    quartet di nomornya sendiri. Mis. nomor "5559" cuma nambah "5"
    SEKALI dan "9" SEKALI (bukan "5" tiga kali) -- supaya pasaran
    yang nomornya kebetulan kembar tidak "nyumbang" lebih berat ke
    ranking dibanding pasaran lain yang nomornya 4 digit unik semua.
    """
    if len(hasil_map) < MIN_PASARAN_UNTUK_HITUNG:
        return None

    counter = Counter({str(d): 0 for d in range(10)})
    for nomor in hasil_map.values():
        digit_unik_di_nomor_ini = set(str(nomor).strip().zfill(4)[-4:])
        for digit in digit_unik_di_nomor_ini:
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

    PENTING (perbaikan bug): untuk pasaran yang draw-nya lebih dari
    1x sehari di jam berbeda (mis. "KK" 17:00 & 23:30, tiap jam bisa
    masuk zona yang beda), kita HARUS ambil nomor dari draw yang
    memang jam-nya cocok ke zona_label ini -- bukan asal ambil draw
    TERAKHIR hari itu seperti versi lama (itu sebabnya sebelum ini
    zona yang bukan pemilik draw terakhir ikut kebawa nomor yang
    salah). Makanya di sini pakai ROW_NUMBER() per (tanggal,
    kode_pasaran) diurutkan sort_key ASC (paling pagi = urutan 0),
    lalu dicocokkan ke "urutan_draw" yang sudah dihitung
    kelompokkan_pasaran() untuk zona ini secara spesifik.
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
        # kode -> urutan_draw yang DIBUTUHKAN zona ini secara spesifik
        # (bukan cuma daftar kode -- 1 kode bisa butuh urutan_draw
        # beda di zona lain, makanya harus per-zona begini)
        urutan_draw_dibutuhkan = {p["kode"]: p["urutan_draw"] for p in zona_pasaran}
        kode_list = list(urutan_draw_dibutuhkan.keys())

        peta_per_tanggal = {t: {} for t in tanggal_belum_ada}

        if kode_list:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT tanggal, kode_pasaran, nomor,
                           ROW_NUMBER() OVER (
                               PARTITION BY tanggal, kode_pasaran
                               ORDER BY sort_key ASC
                           ) - 1 AS urutan_draw
                    FROM history
                    WHERE tanggal = ANY(%s) AND kode_pasaran = ANY(%s);
                    """,
                    (tanggal_belum_ada, kode_list),
                )
                for row in cur.fetchall():
                    # cuma ambil baris yg urutan draw-nya PAS dgn yg
                    # dibutuhkan zona ini utk kode_pasaran tsb --
                    # baris draw lain (mis. draw 23:30 KK saat yg
                    # dibutuhkan zona ini draw 17:00-nya) dilewati
                    dibutuhkan = urutan_draw_dibutuhkan.get(row["kode_pasaran"])
                    if dibutuhkan is not None and row["urutan_draw"] == dibutuhkan:
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
# BARIS TABEL (Periode & Nomor): sekarang dari SEMUA pasaran di 1
# Zona Pasaran (Ddwn1 baru), bukan 1 pasaran acuan seperti dulu
# =========================================================

def ambil_riwayat_zona(conn, opsi, zona_label, jumlah_baris, tanggal_acuan=None):
    """
    N periode/draw terakhir (jumlah_baris) dari SEMUA pasaran yang
    tergabung di 1 zona (opsi + zona_label) -- pengganti
    get_rows_as_of(kode_acuan, ...) sekarang Ddwn1 memilih ZONA
    (bisa banyak pasaran sekaligus), bukan 1 pasaran acuan. Ini
    yang bikin isi tabel lebih relevan & tidak sekaligus me-list
    ratusan pasaran yang tidak berkaitan dengan zona yang dipilih.

    tanggal_acuan: "YYYY-MM-DD" opsional, sama seperti
    get_rows_as_of() -- batas ATAS tanggal yang diambil (None =
    sampai data terbaru/hari ini).

    Untuk pasaran dengan >1 draw/hari (mis. "KK"), cuma draw yang
    urutan waktunya cocok ke zona INI yang diambil (lihat
    "urutan_draw" di kelompokkan_pasaran()) -- draw lain dari
    pasaran yang sama yang masuk zona LAIN tidak ikut kebawa,
    konsisten dengan perbaikan di _ambil_ranking_batch().

    Kalau tanggal_acuan kosong/hari ini: pasaran anggota zona ini
    yang HARI INI belum ada hasilnya di database ikut disisipkan
    sebagai 1 baris "menunggu" per pasaran (nomor=None -- caller
    yang merender jadi "?"), supaya kelihatan pasaran itu memang
    belum draw, bukan cuma hilang dari tabel sampai hasilnya masuk.
    """
    zona_pasaran = kelompokkan_pasaran(opsi).get(zona_label, [])
    urutan_dibutuhkan = {p["kode"]: p["urutan_draw"] for p in zona_pasaran}
    kode_list = list(urutan_dibutuhkan)

    if not kode_list:
        return []

    hari_ini_dmy = _hari_ini_str()  # dd-mm-yyyy, format history.tanggal
    d, m, y = hari_ini_dmy.split("-")
    hari_ini_ymd = f"{y}-{m}-{d}"  # yyyy-mm-dd, format tanggal_acuan/sort_key

    batas_atas = tanggal_acuan or hari_ini_ymd
    tampilkan_menunggu = (batas_atas == hari_ini_ymd)

    baris = []
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT tanggal, kode_pasaran, periode, nomor, sort_key,
                   ROW_NUMBER() OVER (
                       PARTITION BY tanggal, kode_pasaran
                       ORDER BY sort_key ASC
                   ) - 1 AS urutan_draw
            FROM history
            WHERE kode_pasaran = ANY(%s) AND LEFT(sort_key, 10) <= %s
            ORDER BY sort_key DESC
            LIMIT %s;
            """,
            (kode_list, batas_atas, jumlah_baris * len(kode_list)),
        )
        for row in cur.fetchall():
            dibutuhkan = urutan_dibutuhkan.get(row["kode_pasaran"])
            if dibutuhkan is not None and row["urutan_draw"] == dibutuhkan:
                baris.append({
                    "tanggal": row["tanggal"],
                    "periode": row["periode"],
                    "nomor": row["nomor"],
                    "kode_pasaran": row["kode_pasaran"],
                    "sort_key": row["sort_key"],
                })

    baris.sort(key=lambda r: r["sort_key"], reverse=True)
    baris = baris[:jumlah_baris]

    if tampilkan_menunggu:
        kode_sudah_ada_hari_ini = {
            r["kode_pasaran"] for r in baris if r["tanggal"] == hari_ini_dmy
        }
        for p in zona_pasaran:
            if p["kode"] not in kode_sudah_ada_hari_ini:
                baris.append({
                    "tanggal": hari_ini_dmy,
                    "periode": f"{p['kode']} (menunggu)",
                    "nomor": None,
                    "kode_pasaran": p["kode"],
                    "sort_key": f"{hari_ini_ymd}_9999999999",  # ditaruh paling atas hari ini
                })
        baris.sort(key=lambda r: r["sort_key"], reverse=True)
        baris = baris[:jumlah_baris]

    return baris


# =========================================================
# TABEL UTAMA: N draw terakhir dari Zona Pasaran (Ddwn1) + 3 kolom
# opsi (Opsi1/2/3, dari Ddwn2/3/4 -- tetap independen dari Ddwn1)
# =========================================================

def bangun_tabel(zona_pasaran_pilihan, zona_opsi1, zona_opsi2, zona_opsi3, n_digit,
                  jumlah_baris=12, tanggal_acuan=None):
    """
    zona_pasaran_pilihan: value dari Ddwn1 BARU, mis. "Za2" -- kolom
    Periode & Nomor sekarang diisi draw-draw dari SEMUA pasaran yang
    tergabung di zona ini (lihat ambil_riwayat_zona()), bukan cuma 1
    pasaran acuan seperti versi sebelumnya. Baris "menunggu" (belum
    draw hari ini) muncul dengan nomor "?".

    zona_opsi1/2/3, n_digit, jumlah_baris, tanggal_acuan: PERSIS
    seperti sebelumnya -- kolom Opsi1/Opsi2/Opsi3 tetap dihitung
    independen dari zona_pasaran_pilihan, berdasarkan TANGGAL tiap
    baris, lewat Ddwn2/3/4 seperti biasa.

    PERFORMA: sama seperti sebelumnya -- dibatch PER OPSI (bukan per
    tanggal), ditambah cache permanen di analisis_zona_cache (lihat
    _ambil_ranking_batch) supaya tanggal yang sudah pernah dihitung
    dari zona pasaran manapun tidak perlu dihitung ulang.
    """
    n_digit = max(4, min(int(n_digit), 9))
    jumlah_baris = max(1, min(int(jumlah_baris), 100))

    decoded = decode_zona_pasaran(zona_pasaran_pilihan)
    if decoded is None:
        return []
    opsi_pilihan, zona_label_pilihan = decoded

    conn = get_db_connection()
    try:
        rows = ambil_riwayat_zona(conn, opsi_pilihan, zona_label_pilihan, jumlah_baris, tanggal_acuan)
        tanggal_list = list(dict.fromkeys(r["tanggal"] for r in rows))  # dedup, urutan dipertahankan

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
            "nomor": r["nomor"] if r["nomor"] is not None else "?",
            "opsi1": _potong(peta_opsi.get(1, {}).get(t)) if zona_opsi1 else None,
            "opsi2": _potong(peta_opsi.get(2, {}).get(t)) if zona_opsi2 else None,
            "opsi3": _potong(peta_opsi.get(3, {}).get(t)) if zona_opsi3 else None,
        })

    return tabel
