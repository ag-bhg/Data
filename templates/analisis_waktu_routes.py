"""
Route Flask untuk fitur "Analisis Angka per Zona Waktu" (v2).

Cara pasang di app.py yang sudah ada, tambah 2 baris:

    from analisis_waktu_routes import bp_analisis_waktu
    app.register_blueprint(bp_analisis_waktu)

Taruh file ini + analisis_waktu.py + templates/analisis_waktu.html
satu folder dengan app.py & database.py yang sudah ada.
"""

from flask import Blueprint, render_template, request, jsonify

from analisis_waktu import (
    get_zona_list,
    daftar_periode,
    bangun_tabel,
)

bp_analisis_waktu = Blueprint("analisis_waktu", __name__)


@bp_analisis_waktu.route("/analisis-waktu")
def halaman_analisis_waktu():
    return render_template("analisis_waktu.html")


@bp_analisis_waktu.route("/api/analisis-waktu/opsi-tabel")
def api_opsi_tabel():
    """Isi buat Ddwn1 (periode) & Ddwn2/Ddwn3/Ddwn4 (zona tiap opsi)."""
    def _zona_ringkas(opsi):
        return [
            {"label": label, "rentang": f"{mulai} - {akhir}"}
            for label, mulai, akhir in get_zona_list(opsi)
        ]

    return jsonify({
        "periode_list": daftar_periode(),
        "zona_opsi1": _zona_ringkas(1),
        "zona_opsi2": _zona_ringkas(2),
        "zona_opsi3": _zona_ringkas(3),
    })


@bp_analisis_waktu.route("/api/analisis-waktu/tabel")
def api_tabel():
    kode = request.args.get("kode", default="", type=str).strip()
    zona1 = request.args.get("zona1", default="", type=str).strip()
    zona2 = request.args.get("zona2", default="", type=str).strip()
    zona3 = request.args.get("zona3", default="", type=str).strip()
    n_digit = request.args.get("n", default=7, type=int)

    if not kode:
        return jsonify({"error": "Pasaran (Ddwn1) belum dipilih."}), 400

    tabel = bangun_tabel(kode, zona1, zona2, zona3, n_digit)
    return jsonify({"kode": kode, "n_digit": n_digit, "tabel": tabel})
