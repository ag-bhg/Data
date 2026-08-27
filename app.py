import os

from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from scraper import sync_history
from database import init_db, get_rows, count_rows, migrate_sort_keys

app = Flask(__name__)
app.secret_key = "local-demo-only-change-me"

init_db()


@app.route("/")
def index():
    page = max(1, request.args.get("page", 1, type=int))
    per_page = 25

    rows = get_rows(page, per_page)
    total = count_rows()

    pages = max(1, (total + per_page - 1) // per_page)

    return render_template(
        "index.html",
        rows=rows,
        page=page,
        pages=pages,
        total=total
    )


@app.post("/sync")
def sync():
    try:
        inserted = sync_history()

        flash(
            f"Sinkronisasi selesai. {inserted} data diproses.",
            "success"
        )

    except Exception as exc:
        flash(
            f"Gagal mengambil data: {exc}",
            "error"
        )

    return redirect(url_for("index"))


# =========================================================
# CRON OTOMATIS
# =========================================================

@app.get("/api/cron")
def cron_sync():

    # Vercel Cron mengirim User-Agent ini.
    user_agent = request.headers.get("User-Agent", "")

    if "vercel-cron" not in user_agent.lower():
        return jsonify({
            "ok": False,
            "error": "Unauthorized"
        }), 401

    try:
        inserted = sync_history()

        return jsonify({
            "ok": True,
            "message": "Sinkronisasi otomatis berhasil",
            "changed": inserted
        }), 200

    except Exception as exc:

        return jsonify({
            "ok": False,
            "error": str(exc)
        }), 500


# =========================================================
# MIGRASI SATU KALI (isi field sort_key untuk dokumen lama)
#
# Cara pakai:
# 1. Set environment variable MIGRATE_TOKEN di Vercel (string
#    rahasia bebas, misal hasil dari `openssl rand -hex 16`).
# 2. Deploy.
# 3. Buka sekali: https://domain-kamu/api/migrate-sort-key?token=ISI_TOKEN
# 4. Setelah muncul {"ok": true, ...}, migrasi selesai. Endpoint
#    ini aman dipanggil berkali-kali (dokumen yang sudah punya
#    sort_key otomatis dilewati), tapi cukup dijalankan sekali.
# =========================================================

@app.get("/api/migrate-sort-key")
def migrate_sort_key_route():
    token = request.args.get("token", "")
    expected = os.environ.get("MIGRATE_TOKEN", "")

    if not expected or token != expected:
        return jsonify({"ok": False, "error": "Unauthorized"}), 401

    try:
        updated = migrate_sort_keys()

        return jsonify({
            "ok": True,
            "updated": updated
        }), 200

    except Exception as exc:

        return jsonify({
            "ok": False,
            "error": str(exc)
        }), 500


if __name__ == "__main__":
    app.run(
        host="127.0.0.1",
        port=5000,
        debug=True
    )
