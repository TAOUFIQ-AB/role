from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import datetime, timezone

import jwt
from jwt import PyJWKClient
from functools import wraps
from pathlib import Path

from flask import (
    Flask,
    jsonify,
    redirect,
    render_template_string,
    request,
    send_from_directory,
    session,
    url_for,
)
from werkzeug.utils import secure_filename


DATA_DIR = Path(os.environ.get("DATA_DIR", "/data")).resolve()
MEDIA_DIR = DATA_DIR / "media"
DB_PATH = DATA_DIR / "control.db"
DATA_DIR.mkdir(parents=True, exist_ok=True)
MEDIA_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-me")
app.config["MAX_CONTENT_LENGTH"] = int(os.environ.get("MAX_UPLOAD_MB", "120")) * 1024 * 1024

DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "")
AGENT_TOKEN = os.environ.get("AGENT_TOKEN", "")
GITHUB_REPOSITORY_ALLOW = os.environ.get(
    "GITHUB_REPOSITORY_ALLOW", "TAOUFIQ-AB/role"
).strip()
GITHUB_OIDC_AUDIENCE = os.environ.get(
    "GITHUB_OIDC_AUDIENCE", "reels-hunter-dashboard"
).strip()
_GITHUB_JWKS = PyJWKClient(
    "https://token.actions.githubusercontent.com/.well-known/jwks"
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_db() -> None:
    conn = db()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            reel_id TEXT UNIQUE NOT NULL,
            reel_url TEXT NOT NULL,
            review_status TEXT NOT NULL DEFAULT 'scanned',
            ai_decision TEXT NOT NULL DEFAULT '',
            ai_reason TEXT NOT NULL DEFAULT '',
            views INTEGER NOT NULL DEFAULT 0,
            likes INTEGER NOT NULL DEFAULT 0,
            metrics_source TEXT NOT NULL DEFAULT '',
            metrics_confidence TEXT NOT NULL DEFAULT '',
            discovery_score REAL NOT NULL DEFAULT 0,
            queries TEXT NOT NULL DEFAULT '',
            caption TEXT NOT NULL DEFAULT '',
            preview_file TEXT NOT NULL DEFAULT '',
            video_file TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS commands (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            command TEXT NOT NULL,
            payload TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'queued',
            created_at TEXT NOT NULL,
            delivered_at TEXT
        );

        CREATE TABLE IF NOT EXISTS agent_state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )
    conn.commit()
    conn.close()


init_db()


def ui_auth_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("ok"):
            return redirect(url_for("login", next=request.path))
        return fn(*args, **kwargs)
    return wrapper


def _valid_github_oidc(token: str) -> bool:
    if not token or token.count(".") != 2:
        return False
    try:
        signing_key = _GITHUB_JWKS.get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=GITHUB_OIDC_AUDIENCE,
            issuer="https://token.actions.githubusercontent.com",
            options={"require": ["exp", "iat", "iss", "aud", "repository"]},
        )
        repository = str(claims.get("repository") or "")
        event_name = str(claims.get("event_name") or "")
        return (
            repository == GITHUB_REPOSITORY_ALLOW
            and event_name in {"push", "schedule", "workflow_dispatch"}
        )
    except Exception:
        return False


def agent_auth_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        static = request.headers.get("X-Agent-Token", "").strip()
        bearer = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()

        static_ok = bool(AGENT_TOKEN and static and static == AGENT_TOKEN)
        oidc_ok = _valid_github_oidc(bearer)
        if not (static_ok or oidc_ok):
            return jsonify({"error": "unauthorized"}), 401
        return fn(*args, **kwargs)
    return wrapper


LOGIN_HTML = """
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Reels Hunter Control</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#07090d;color:#eef2ff;font-family:Inter,ui-sans-serif,system-ui,Segoe UI,Arial;min-height:100vh;display:grid;place-items:center}
.card{width:min(420px,92vw);background:linear-gradient(180deg,#111621,#0d1118);border:1px solid #20293a;border-radius:24px;padding:30px;box-shadow:0 30px 90px #0008}
.badge{display:inline-flex;gap:8px;align-items:center;font-size:12px;color:#9fb0ca;background:#0a0e15;border:1px solid #253048;padding:7px 10px;border-radius:999px}
h1{font-size:28px;margin:20px 0 8px}p{color:#8f9bb0;margin:0 0 22px;line-height:1.5}
input{width:100%;padding:14px 15px;border-radius:14px;border:1px solid #293349;background:#090d13;color:white;outline:none;font-size:16px}
button{width:100%;margin-top:12px;padding:14px;border:0;border-radius:14px;background:#eef2ff;color:#0a0d12;font-weight:800;cursor:pointer}
.err{color:#ff8f9d;font-size:13px;margin:12px 0 0}
</style></head>
<body><form class="card" method="post">
<span class="badge">● Private control panel</span>
<h1>Reels Hunter</h1><p>Review GTA VI discoveries, approve or reject candidates, and control the active hunter.</p>
<input name="password" type="password" placeholder="Dashboard password" autofocus>
<button type="submit">Open dashboard</button>
{% if error %}<div class="err">{{ error }}</div>{% endif %}
</form></body></html>
"""


DASHBOARD_HTML = """
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Reels Hunter Control</title>
<style>
:root{--bg:#07090d;--panel:#0d1118;--panel2:#111722;--line:#20293a;--muted:#8895aa;--text:#f4f6fb;--green:#67e8a5;--red:#ff7586;--amber:#f6c85f;--blue:#6da8ff}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 10% 0%,#111827 0,transparent 28%),var(--bg);color:var(--text);font-family:Inter,ui-sans-serif,system-ui,Segoe UI,Arial}
.shell{max-width:1500px;margin:auto;padding:24px}.top{display:flex;gap:18px;align-items:center;justify-content:space-between;margin-bottom:18px}.brand h1{margin:0;font-size:28px}.brand p{margin:5px 0 0;color:var(--muted);font-size:13px}.right{display:flex;gap:10px;align-items:center}.pill{padding:8px 11px;border-radius:999px;border:1px solid var(--line);background:#0a0e14;color:#aeb9ca;font-size:12px}.dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--amber);margin-right:6px}.dot.live{background:var(--green);box-shadow:0 0 14px #67e8a588}
.grid{display:grid;grid-template-columns:330px 1fr;gap:18px}.panel{background:linear-gradient(180deg,var(--panel2),var(--panel));border:1px solid var(--line);border-radius:20px;padding:16px;box-shadow:0 18px 50px #0004}
.stats{display:grid;grid-template-columns:repeat(2,1fr);gap:10px;margin-bottom:16px}.stat{background:#090d13;border:1px solid #1b2434;border-radius:15px;padding:13px}.stat b{display:block;font-size:24px}.stat span{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.08em}
h3{margin:8px 0 12px;font-size:14px;color:#dce4f5}.controls{display:grid;grid-template-columns:repeat(2,1fr);gap:8px}.btn{border:1px solid #29344a;background:#0a0f17;color:white;padding:10px;border-radius:12px;font-weight:700;cursor:pointer}.btn:hover{background:#131b28}.btn.green{border-color:#245a43;color:#8df2bb}.btn.red{border-color:#67303b;color:#ff9aa8}.btn.amber{border-color:#66572e;color:#f7d77b}
.field{display:grid;grid-template-columns:1fr 92px;gap:8px;align-items:center;margin:8px 0;color:#9ca9bc;font-size:12px}.field input{background:#090d13;border:1px solid #253047;color:white;border-radius:10px;padding:9px;width:100%}
.filters{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:14px}.filter{border:1px solid #263149;background:#0a0e15;color:#9eabc0;border-radius:999px;padding:8px 11px;cursor:pointer;font-size:12px}.filter.active{background:#eef2ff;color:#0a0d12;border-color:#eef2ff}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(285px,1fr));gap:14px}.card{overflow:hidden;background:#090d13;border:1px solid #1d2738;border-radius:18px}.media{aspect-ratio:9/13;background:#030507;display:grid;place-items:center;overflow:hidden}.media img,.media video{width:100%;height:100%;object-fit:cover}.empty{color:#57657a;font-size:12px;text-align:center;padding:20px}.body{padding:13px}.row{display:flex;justify-content:space-between;gap:10px;align-items:center}.rid{font-family:ui-monospace,monospace;font-size:12px;color:#d8e1f2}.status{font-size:10px;text-transform:uppercase;font-weight:800;letter-spacing:.08em;padding:6px 8px;border-radius:999px;background:#18202e;color:#aebbd0}.status.pending{background:#332c16;color:#ffd979}.status.approved{background:#173127;color:#8ef2bb}.status.rejected,.status.ai_rejected{background:#351d24;color:#ff9bab}
.meta{display:grid;grid-template-columns:repeat(2,1fr);gap:7px;margin:11px 0}.m{background:#0d121b;border-radius:10px;padding:8px}.m b{display:block;font-size:14px}.m span{font-size:10px;color:#768399}.reason{font-size:11px;color:#8b98ac;line-height:1.45;min-height:32px}.actions{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:11px}.actions button{border:0;border-radius:11px;padding:10px;font-weight:800;cursor:pointer}.approve{background:#8df2bb;color:#092015}.reject{background:#ff9aa8;color:#2a0a0e}.open{display:block;text-align:center;margin-top:8px;border:1px solid #263149;padding:9px;border-radius:10px;color:#a9b8ce;text-decoration:none;font-size:11px}
.small{font-size:11px;color:#7f8ca0}.log{margin-top:12px;color:#8da1bc;font-size:11px;min-height:16px}
@media(max-width:900px){.grid{grid-template-columns:1fr}.shell{padding:14px}.top{align-items:flex-start}.right{flex-wrap:wrap;justify-content:flex-end}}
</style>
</head>
<body>
<div class="shell">
  <div class="top">
    <div class="brand"><h1>Reels Hunter Control</h1><p>GTA VI discovery review + live agent controls</p></div>
    <div class="right"><span class="pill"><span id="dot" class="dot"></span><span id="agentText">checking agent…</span></span><a class="pill" style="text-decoration:none" href="/logout">Log out</a></div>
  </div>
  <div class="grid">
    <aside class="panel">
      <div class="stats">
        <div class="stat"><b id="sTotal">0</b><span>Total</span></div>
        <div class="stat"><b id="sPending">0</b><span>Pending</span></div>
        <div class="stat"><b id="sApproved">0</b><span>Approved</span></div>
        <div class="stat"><b id="sRejected">0</b><span>Rejected</span></div>
      </div>
      <h3>Agent controls</h3>
      <div class="controls">
        <button class="btn green" onclick="command('start')">Start hunt</button>
        <button class="btn amber" onclick="command('pause')">Pause</button>
        <button class="btn" onclick="command('resume')">Resume</button>
        <button class="btn red" onclick="command('stop')">Stop</button>
        <button class="btn" onclick="command('skip')">Skip discovery</button>
      </div>
      <h3 style="margin-top:18px">Runtime settings</h3>
      <div class="field"><span>Minimum views</span><input id="minViews" value="50000"></div>
      <div class="field"><span>Minimum likes</span><input id="minLikes" value="0"></div>
      <div class="field"><span>Scan target</span><input id="scanTarget" value="35"></div>
      <div class="field"><span>Max approved</span><input id="maxSend" value="5"></div>
      <button class="btn" style="width:100%;margin-top:6px" onclick="saveSettings()">Send settings to agent</button>
      <div id="controlLog" class="log"></div>
      <div style="margin-top:20px" class="small">Commands are delivered when a GitHub Actions hunter is online. Review decisions remain stored on Railway.</div>
    </aside>

    <main class="panel">
      <div class="filters">
        <button class="filter active" data-filter="all">All</button>
        <button class="filter" data-filter="pending">Pending review</button>
        <button class="filter" data-filter="approved">Approved</button>
        <button class="filter" data-filter="rejected">Rejected</button>
        <button class="filter" data-filter="ai_rejected">AI rejected</button>
        <button class="filter" data-filter="scanned">Scanned</button>
      </div>
      <div id="cards" class="cards"></div>
    </main>
  </div>
</div>
<script>
let currentFilter='all';
const esc=s=>String(s??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
const fmt=n=>Number(n||0).toLocaleString();

async function api(url, options={}) {
  const r=await fetch(url,{...options,headers:{'Content-Type':'application/json',...(options.headers||{})}});
  if(!r.ok) throw new Error(await r.text());
  return r.json();
}
function mediaHtml(x){
  if(x.video_url) return '<video controls preload="metadata" src="'+esc(x.video_url)+'"></video>';
  if(x.preview_url) return '<img loading="lazy" src="'+esc(x.preview_url)+'">';
  return '<div class="empty">No stored media yet<br>Open the Instagram source below.</div>';
}
function card(x){
  return `<article class="card">
    <div class="media">${mediaHtml(x)}</div>
    <div class="body">
      <div class="row"><span class="rid">${esc(x.reel_id)}</span><span class="status ${esc(x.review_status)}">${esc(x.review_status)}</span></div>
      <div class="meta">
        <div class="m"><b>${fmt(x.views)}</b><span>views</span></div>
        <div class="m"><b>${fmt(x.likes)}</b><span>likes</span></div>
        <div class="m"><b>${esc(x.ai_decision||'—')}</b><span>AI decision</span></div>
        <div class="m"><b>${Number(x.discovery_score||0).toFixed(1)}</b><span>discovery score</span></div>
      </div>
      <div class="reason">${esc(x.ai_reason||x.metrics_source||'')}</div>
      <div class="actions">
        <button class="approve" onclick="decide(${x.id},'approved')">Approve</button>
        <button class="reject" onclick="decide(${x.id},'rejected')">Reject</button>
      </div>
      <a class="open" href="${esc(x.reel_url)}" target="_blank" rel="noopener">Open on Instagram ↗</a>
    </div>
  </article>`;
}
async function load(){
  try{
    const d=await api('/api/items?status='+encodeURIComponent(currentFilter));
    document.getElementById('cards').innerHTML=d.items.length?d.items.map(card).join(''):'<div class="empty">Nothing in this filter yet.</div>';
    document.getElementById('sTotal').textContent=d.stats.total;
    document.getElementById('sPending').textContent=d.stats.pending;
    document.getElementById('sApproved').textContent=d.stats.approved;
    document.getElementById('sRejected').textContent=d.stats.rejected;
    const live=d.agent && d.agent.online;
    document.getElementById('dot').className='dot'+(live?' live':'');
    document.getElementById('agentText').textContent=live?(d.agent.state||'online'):'agent offline';
  }catch(e){document.getElementById('controlLog').textContent='Load error: '+e.message}
}
async function decide(id,decision){
  await api('/api/items/'+id+'/decision',{method:'POST',body:JSON.stringify({decision})}); await load();
}
async function command(action,payload={}){
  try{
    await api('/api/control',{method:'POST',body:JSON.stringify({action,payload})});
    document.getElementById('controlLog').textContent='Queued: '+action;
  }catch(e){document.getElementById('controlLog').textContent='Control error: '+e.message}
}
function saveSettings(){
  command('config',{
    min_views:document.getElementById('minViews').value,
    min_likes:document.getElementById('minLikes').value,
    target_reels:document.getElementById('scanTarget').value,
    max_send:document.getElementById('maxSend').value
  });
}
document.querySelectorAll('.filter').forEach(b=>b.onclick=()=>{
  document.querySelectorAll('.filter').forEach(x=>x.classList.remove('active')); b.classList.add('active');
  currentFilter=b.dataset.filter; load();
});
load(); setInterval(load,5000);
</script>
</body></html>
"""


@app.get("/health")
def health():
    return jsonify({"ok": True})


@app.route("/login", methods=["GET", "POST"])
def login():
    error = ""
    if request.method == "POST":
        if DASHBOARD_PASSWORD and request.form.get("password", "") == DASHBOARD_PASSWORD:
            session["ok"] = True
            return redirect(request.args.get("next") or url_for("index"))
        error = "Wrong password."
    return render_template_string(LOGIN_HTML, error=error)


@app.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.get("/")
@ui_auth_required
def index():
    return render_template_string(DASHBOARD_HTML)


@app.get("/media/<path:name>")
@ui_auth_required
def media(name: str):
    return send_from_directory(MEDIA_DIR, name, conditional=True)


def stats_payload(conn: sqlite3.Connection) -> dict:
    rows = conn.execute(
        "SELECT review_status, COUNT(*) AS n FROM reviews GROUP BY review_status"
    ).fetchall()
    counts = {r["review_status"]: r["n"] for r in rows}
    return {
        "total": sum(counts.values()),
        "pending": counts.get("pending", 0),
        "approved": counts.get("approved", 0),
        "rejected": counts.get("rejected", 0),
        "ai_rejected": counts.get("ai_rejected", 0),
        "scanned": counts.get("scanned", 0),
    }


def agent_payload(conn: sqlite3.Connection) -> dict:
    row = conn.execute(
        "SELECT value, updated_at FROM agent_state WHERE key='heartbeat'"
    ).fetchone()
    if not row:
        return {"online": False, "state": "offline"}
    try:
        payload = json.loads(row["value"])
    except Exception:
        payload = {"state": row["value"]}
    try:
        updated = datetime.fromisoformat(row["updated_at"])
        age = (datetime.now(timezone.utc) - updated).total_seconds()
    except Exception:
        age = 999999
    payload["online"] = age < 45
    return payload


@app.get("/api/items")
@ui_auth_required
def api_items():
    status = request.args.get("status", "all")
    conn = db()
    params: list[object] = []
    where = ""
    if status != "all":
        where = "WHERE review_status=?"
        params.append(status)

    rows = conn.execute(
        f"SELECT * FROM reviews {where} ORDER BY updated_at DESC LIMIT 200",
        params,
    ).fetchall()

    items = []
    for row in rows:
        d = dict(row)
        d["preview_url"] = (
            url_for("media", name=d["preview_file"]) if d["preview_file"] else ""
        )
        d["video_url"] = (
            url_for("media", name=d["video_file"]) if d["video_file"] else ""
        )
        items.append(d)

    result = {
        "items": items,
        "stats": stats_payload(conn),
        "agent": agent_payload(conn),
    }
    conn.close()
    return jsonify(result)


@app.post("/api/items/<int:item_id>/decision")
@ui_auth_required
def decision(item_id: int):
    body = request.get_json(silent=True) or {}
    choice = str(body.get("decision", "")).strip().lower()
    if choice not in {"approved", "rejected", "pending"}:
        return jsonify({"error": "invalid decision"}), 400
    conn = db()
    conn.execute(
        "UPDATE reviews SET review_status=?, updated_at=? WHERE id=?",
        (choice, now_iso(), item_id),
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "decision": choice})


@app.post("/api/control")
@ui_auth_required
def control():
    body = request.get_json(silent=True) or {}
    action = str(body.get("action", "")).strip().lower()
    payload = body.get("payload") or {}
    allowed = {"start", "pause", "resume", "stop", "skip", "config"}
    if action not in allowed:
        return jsonify({"error": "invalid action"}), 400

    conn = db()
    conn.execute(
        "INSERT INTO commands(command,payload,status,created_at) VALUES(?,?,?,?)",
        (action, json.dumps(payload), "queued", now_iso()),
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "queued": action})


@app.post("/api/agent/heartbeat")
@agent_auth_required
def agent_heartbeat():
    payload = request.get_json(silent=True) or {}
    conn = db()
    conn.execute(
        """
        INSERT INTO agent_state(key,value,updated_at) VALUES('heartbeat',?,?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
        """,
        (json.dumps(payload), now_iso()),
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.get("/api/agent/commands")
@agent_auth_required
def agent_commands():
    conn = db()
    rows = conn.execute(
        "SELECT id,command,payload FROM commands WHERE status='queued' ORDER BY id LIMIT 20"
    ).fetchall()
    ids = [r["id"] for r in rows]
    if ids:
        placeholders = ",".join("?" for _ in ids)
        conn.execute(
            f"UPDATE commands SET status='delivered', delivered_at=? WHERE id IN ({placeholders})",
            [now_iso(), *ids],
        )
        conn.commit()
    out = []
    for row in rows:
        try:
            payload = json.loads(row["payload"] or "{}")
        except Exception:
            payload = {}
        out.append({"id": row["id"], "command": row["command"], "payload": payload})
    conn.close()
    return jsonify({"commands": out})


@app.post("/api/agent/ingest")
@agent_auth_required
def agent_ingest():
    reel_id = re.sub(r"[^A-Za-z0-9_-]", "", request.form.get("reel_id", ""))[:80]
    reel_url = request.form.get("reel_url", "").strip()
    if not reel_id or not reel_url:
        return jsonify({"error": "reel_id and reel_url required"}), 400

    def intval(name: str) -> int:
        try:
            return int(float(request.form.get(name, "0") or 0))
        except Exception:
            return 0

    def floatval(name: str) -> float:
        try:
            return float(request.form.get(name, "0") or 0)
        except Exception:
            return 0.0

    ai_decision = request.form.get("ai_decision", "").strip().upper()
    incoming_status = request.form.get("review_status", "").strip().lower()
    if incoming_status not in {"scanned", "pending", "ai_rejected", "approved", "rejected"}:
        incoming_status = "pending" if ai_decision == "PASSED" else (
            "ai_rejected" if ai_decision == "FAILED" else "scanned"
        )

    preview_file = ""
    video_file = ""

    preview = request.files.get("preview")
    if preview and preview.filename:
        ext = Path(secure_filename(preview.filename)).suffix.lower() or ".jpg"
        preview_file = f"{reel_id}_preview{ext}"
        preview.save(MEDIA_DIR / preview_file)

    video = request.files.get("video")
    if video and video.filename:
        ext = Path(secure_filename(video.filename)).suffix.lower() or ".mp4"
        video_file = f"{reel_id}{ext}"
        video.save(MEDIA_DIR / video_file)

    conn = db()
    existing = conn.execute(
        "SELECT review_status,preview_file,video_file FROM reviews WHERE reel_id=?",
        (reel_id,),
    ).fetchone()

    # Never overwrite an explicit human decision with a later agent update.
    if existing and existing["review_status"] in {"approved", "rejected"}:
        final_status = existing["review_status"]
    else:
        final_status = incoming_status

    if existing:
        preview_file = preview_file or existing["preview_file"]
        video_file = video_file or existing["video_file"]

    stamp = now_iso()
    conn.execute(
        """
        INSERT INTO reviews(
            reel_id,reel_url,review_status,ai_decision,ai_reason,views,likes,
            metrics_source,metrics_confidence,discovery_score,queries,caption,
            preview_file,video_file,created_at,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(reel_id) DO UPDATE SET
            reel_url=excluded.reel_url,
            review_status=excluded.review_status,
            ai_decision=excluded.ai_decision,
            ai_reason=excluded.ai_reason,
            views=excluded.views,
            likes=excluded.likes,
            metrics_source=excluded.metrics_source,
            metrics_confidence=excluded.metrics_confidence,
            discovery_score=excluded.discovery_score,
            queries=excluded.queries,
            caption=excluded.caption,
            preview_file=excluded.preview_file,
            video_file=excluded.video_file,
            updated_at=excluded.updated_at
        """,
        (
            reel_id,
            reel_url,
            final_status,
            ai_decision,
            request.form.get("ai_reason", "")[:1000],
            intval("views"),
            intval("likes"),
            request.form.get("metrics_source", "")[:120],
            request.form.get("metrics_confidence", "")[:40],
            floatval("discovery_score"),
            request.form.get("queries", "")[:1000],
            request.form.get("caption", "")[:2000],
            preview_file,
            video_file,
            stamp,
            stamp,
        ),
    )
    conn.commit()
    row = conn.execute(
        "SELECT id,review_status FROM reviews WHERE reel_id=?",
        (reel_id,),
    ).fetchone()
    conn.close()
    return jsonify({"ok": True, "id": row["id"], "status": row["review_status"]})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
