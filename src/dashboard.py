"""
Web-Dashboard für WiFi Motion Detection
Zeigt Live-Score, Ereignisse und CSI-Graph im Browser.

Start: python3 dashboard.py
Öffne: http://<Pi-IP>:5000
"""

import json, logging, os, socket, sys, threading, time
from collections import deque
from datetime import datetime
from pathlib import Path

import numpy as np

# Flask + SocketIO (WebSocket für Echtzeit-Updates)
try:
    from flask import Flask, jsonify, render_template_string
    from flask_socketio import SocketIO, emit
except ImportError:
    print("[!] pip3 install flask flask-socketio")
    sys.exit(1)

from csi_parser import parse as parse_frame
from signal_processing import CSIPipeline

# ── Konfiguration ─────────────────────────────────────────────────────────────

def load_config():
    p = Path(__file__).parent.parent / "config.json"
    return json.loads(p.read_text()) if p.exists() else {}

cfg      = load_config()
det_cfg  = cfg.get("detection", {})
srv_cfg  = cfg.get("server", {})

THRESHOLD   = det_cfg.get("threshold", 15.0)
MAX_HISTORY = 200   # Punkte im Live-Graph
CSI_PORT    = int(os.environ.get("CSI_PORT", 5500))

# ── Flask App ─────────────────────────────────────────────────────────────────

app = Flask(__name__)
app.config["SECRET_KEY"] = os.urandom(16).hex()
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# Gemeinsamer Zustand
state = {
    "scores":        deque(maxlen=MAX_HISTORY),
    "timestamps":    deque(maxlen=MAX_HISTORY),
    "events":        [],
    "frame_count":   0,
    "current_score": 0.0,
    "motion":        False,
    "rssi":          0,
    "lock":          threading.Lock(),
}

pipeline = CSIPipeline(
    baseline_window  = det_cfg.get("baseline_window", 100),
    detection_window = det_cfg.get("detection_window", 10),
    pca_components   = det_cfg.get("pca_components", 5),
    bandpass_low     = det_cfg.get("bandpass_low_hz", 0.1),
    bandpass_high    = det_cfg.get("bandpass_high_hz", 2.0),
    sample_rate      = det_cfg.get("sample_rate_hz", 100.0),
)

# ── HTML-Dashboard ────────────────────────────────────────────────────────────

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>WiFi Motion Detection</title>
<script src="https://cdn.socket.io/4.7.2/socket.io.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  :root {
    --bg: #0f1117; --card: #1a1d27; --accent: #3b82f6;
    --green: #22c55e; --red: #ef4444; --yellow: #f59e0b;
    --text: #e2e8f0; --muted: #64748b;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font-family: system-ui, sans-serif;
         min-height: 100vh; padding: 1.5rem; }
  h1 { font-size: 1.3rem; font-weight: 600; margin-bottom: 1.5rem;
       display: flex; align-items: center; gap: .6rem; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
          gap: 1rem; margin-bottom: 1.5rem; }
  .card { background: var(--card); border-radius: 12px; padding: 1.2rem; }
  .card .label { font-size: .75rem; color: var(--muted); text-transform: uppercase;
                 letter-spacing: .05em; margin-bottom: .4rem; }
  .card .value { font-size: 2rem; font-weight: 700; }
  #status-badge { display: inline-block; padding: .3rem .9rem; border-radius: 999px;
                  font-size: .85rem; font-weight: 600; transition: all .3s; }
  .badge-ok     { background: #14532d; color: var(--green); }
  .badge-motion { background: #7f1d1d; color: var(--red); animation: pulse .6s infinite alternate; }
  @keyframes pulse { from { opacity: 1 } to { opacity: .6 } }
  .chart-card { background: var(--card); border-radius: 12px; padding: 1.2rem;
                margin-bottom: 1.5rem; }
  .chart-card h2 { font-size: .9rem; color: var(--muted); margin-bottom: 1rem; }
  canvas { max-height: 200px; }
  .events { background: var(--card); border-radius: 12px; padding: 1.2rem; }
  .events h2 { font-size: .9rem; color: var(--muted); margin-bottom: .8rem; }
  .event-list { max-height: 220px; overflow-y: auto; }
  .event-item { display: flex; justify-content: space-between; align-items: center;
                padding: .5rem .7rem; border-radius: 6px; margin-bottom: .3rem;
                background: #252836; font-size: .82rem; }
  .event-item .score { color: var(--yellow); font-weight: 600; }
  .event-item .ts    { color: var(--muted); font-size: .75rem; }
  .dot { width: 10px; height: 10px; border-radius: 50%; background: var(--green);
         display: inline-block; margin-right: .4rem; }
  .dot.red { background: var(--red); }
</style>
</head>
<body>
<h1>
  <span class="dot" id="conn-dot"></span>
  WiFi Motion Detection
  &nbsp;
  <span id="status-badge" class="badge-ok">Ruhig</span>
</h1>

<div class="grid">
  <div class="card">
    <div class="label">Bewegungs-Score</div>
    <div class="value" id="score">0.00</div>
  </div>
  <div class="card">
    <div class="label">Schwellwert</div>
    <div class="value" id="threshold">--</div>
  </div>
  <div class="card">
    <div class="label">RSSI</div>
    <div class="value" id="rssi">-- dBm</div>
  </div>
  <div class="card">
    <div class="label">Frames empfangen</div>
    <div class="value" id="frames">0</div>
  </div>
  <div class="card">
    <div class="label">Ereignisse</div>
    <div class="value" id="events-count">0</div>
  </div>
</div>

<div class="chart-card">
  <h2>Live-Score (letzte {{ max_history }} Frames)</h2>
  <canvas id="chart"></canvas>
</div>

<div class="events">
  <h2>Letzte Bewegungsereignisse</h2>
  <div class="event-list" id="event-list">
    <div style="color:var(--muted);font-size:.82rem">Noch keine Ereignisse.</div>
  </div>
</div>

<script>
const THRESHOLD = {{ threshold }};
const socket = io();
const dot = document.getElementById('conn-dot');

socket.on('connect',    () => { dot.classList.remove('red'); });
socket.on('disconnect', () => { dot.classList.add('red'); });

// Chart initialisieren
const ctx = document.getElementById('chart').getContext('2d');
const chart = new Chart(ctx, {
  type: 'line',
  data: {
    labels: [],
    datasets: [
      { label: 'Score', data: [], borderColor: '#3b82f6', backgroundColor: 'rgba(59,130,246,.1)',
        borderWidth: 1.5, pointRadius: 0, fill: true, tension: 0.3 },
      { label: 'Schwelle', data: [], borderColor: '#ef4444', borderDash: [4,4],
        borderWidth: 1.2, pointRadius: 0, fill: false },
    ]
  },
  options: {
    animation: false,
    responsive: true,
    plugins: { legend: { display: false } },
    scales: {
      x: { display: false },
      y: { beginAtZero: true, grid: { color: '#1e2130' },
           ticks: { color: '#64748b', font: { size: 11 } } }
    }
  }
});

// Update empfangen
socket.on('update', d => {
  document.getElementById('score').textContent   = d.score.toFixed(2);
  document.getElementById('threshold').textContent = d.threshold.toFixed(1);
  document.getElementById('rssi').textContent    = d.rssi + ' dBm';
  document.getElementById('frames').textContent  = d.frame_count.toLocaleString();
  document.getElementById('events-count').textContent = d.events_total;

  const badge = document.getElementById('status-badge');
  if (d.motion) {
    badge.textContent = 'BEWEGUNG!';
    badge.className   = 'badge-motion';
  } else {
    badge.textContent = 'Ruhig';
    badge.className   = 'badge-ok';
  }

  // Chart aktualisieren
  chart.data.labels.push('');
  chart.data.datasets[0].data.push(d.score);
  chart.data.datasets[1].data.push(d.threshold);
  if (chart.data.labels.length > {{ max_history }}) {
    chart.data.labels.shift();
    chart.data.datasets.forEach(ds => ds.data.shift());
  }
  chart.update('none');
});

// Ereignisse empfangen
socket.on('motion_event', e => {
  const list = document.getElementById('event-list');
  if (list.querySelector('div[style]')) list.innerHTML = '';
  const div = document.createElement('div');
  div.className = 'event-item';
  div.innerHTML = `<span class="score">Score: ${e.score.toFixed(2)}</span>
                   <span class="ts">${e.timestamp}</span>`;
  list.prepend(div);
  if (list.children.length > 50) list.lastChild.remove();
});
</script>
</body>
</html>"""


# ── API-Routen ────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template_string(DASHBOARD_HTML,
                                  threshold=THRESHOLD,
                                  max_history=MAX_HISTORY)

@app.route("/api/status")
def api_status():
    with state["lock"]:
        return jsonify({
            "frame_count":   state["frame_count"],
            "score":         state["current_score"],
            "threshold":     THRESHOLD,
            "motion":        state["motion"],
            "rssi":          state["rssi"],
            "events_total":  len(state["events"]),
            "last_event":    state["events"][-1] if state["events"] else None,
        })

@app.route("/api/events")
def api_events():
    with state["lock"]:
        return jsonify(state["events"][-50:])


# ── CSI-Empfänger (Background-Thread) ────────────────────────────────────────

def csi_receiver():
    cooldown_frames = det_cfg.get("cooldown_frames", 30)
    since_event = cooldown_frames
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", CSI_PORT))
    sock.settimeout(1.0)
    logging.info(f"CSI Receiver lauscht auf UDP:{CSI_PORT}")

    while True:
        try: data, _ = sock.recvfrom(4096)
        except socket.timeout: continue
        except Exception as e: logging.error(f"Socket-Fehler: {e}"); break

        frame = parse_frame(data)
        if frame is None: continue

        amp   = np.array(frame.amplitudes)
        score = pipeline.push(amp) or 0.0
        since_event += 1

        motion = score > THRESHOLD and since_event >= cooldown_frames

        with state["lock"]:
            state["frame_count"]   += 1
            state["current_score"]  = score
            state["motion"]         = motion
            state["rssi"]           = frame.rssi
            state["scores"].append(score)
            state["timestamps"].append(frame.timestamp)

            if motion:
                since_event = 0
                event = {"timestamp": datetime.now().isoformat(),
                         "frame": state["frame_count"],
                         "score": round(score, 2)}
                state["events"].append(event)
                socketio.emit("motion_event", event)
                logging.warning(f"BEWEGUNG | Score: {score:.2f}")

        # Live-Update alle 5 Frames senden
        if state["frame_count"] % 5 == 0:
            socketio.emit("update", {
                "score":       round(score, 2),
                "threshold":   THRESHOLD,
                "rssi":        frame.rssi,
                "frame_count": state["frame_count"],
                "motion":      motion,
                "events_total": len(state["events"]),
            })


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s")

    t = threading.Thread(target=csi_receiver, daemon=True)
    t.start()

    host = srv_cfg.get("host", "0.0.0.0")
    port = int(srv_cfg.get("port", 5000))
    logging.info(f"Dashboard: http://{host}:{port}")
    socketio.run(app, host=host, port=port, allow_unsafe_werkzeug=True)
