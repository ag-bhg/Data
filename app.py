from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from scraper import sync_history
from database import init_db, get_rows, count_rows

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
            f"Sinkronisasi selesai. {inserted} data baru/berubah disimpan.",
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


if __name__ == "__main__":
    app.run(
        host="127.0.0.1",
        port=5000,
        debug=True
    )
