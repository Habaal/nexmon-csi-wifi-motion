"""
WiFi Motion Detection via Nexmon CSI
Erkennt Bewegungen anhand von CSI-Amplituden-Veränderungen.

Verwendung:
  sudo python3 motion_detection.py --live
  python3 motion_detection.py --input csi_data.csv --plot
"""

import argparse, csv, json, logging, os, signal, socket, sys, time, threading
from datetime import datetime
from pathlib import Path

import numpy as np
from csi_parser import parse as parse_frame
from signal_processing import CSIPipeline

CSI_UDP_PORT = 5500

def load_config(path="config.json"):
    cfg_path = Path(__file__).parent.parent / path
    return json.loads(cfg_path.read_text()) if cfg_path.exists() else {}

def setup_logging(cfg):
    log_cfg = cfg.get("logging", {})
    level   = getattr(logging, log_cfg.get("level", "INFO"))
    log_file = log_cfg.get("file", "/var/log/nexmon-motion.log")
    handlers = [logging.StreamHandler()]
    try:    handlers.append(logging.FileHandler(log_file))
    except PermissionError: pass
    logging.basicConfig(level=level,
        format="%(asctime)s [%(levelname)s] %(message)s", handlers=handlers)


# ── OPCHAT-Benachrichtigung ───────────────────────────────────────────────────

class OpchatNotifier:
    def __init__(self, cfg):
        n = cfg.get("notifications", {})
        self.enabled  = n.get("opchat_enabled", False)
        self.url      = n.get("opchat_url", "")
        self.room     = n.get("opchat_room", "")
        self.token    = n.get("opchat_token", "")
        self.cooldown = n.get("cooldown_seconds", 30)
        self._last    = 0

    def notify(self, event):
        if not self.enabled or not self.url: return
        if time.time() - self._last < self.cooldown: return
        try:
            import urllib.request
            msg = f"Bewegung erkannt! Score: {event['score']:.1f} | {event['timestamp']}"
            data = json.dumps({"room": self.room, "message": msg,
                               "token": self.token}).encode()
            req  = urllib.request.Request(self.url, data=data,
                     headers={"Content-Type": "application/json"}, method="POST")
            urllib.request.urlopen(req, timeout=5)
            self._last = time.time()
            logging.info("OPCHAT-Benachrichtigung gesendet.")
        except Exception as e:
            logging.warning(f"OPCHAT notify fehlgeschlagen: {e}")


# ── Bewegungsdetektor ─────────────────────────────────────────────────────────

class MotionDetector:
    def __init__(self, cfg, on_motion=None):
        det = cfg.get("detection", {})
        self._pipeline = CSIPipeline(
            baseline_window  = det.get("baseline_window", 100),
            detection_window = det.get("detection_window", 10),
            pca_components   = det.get("pca_components", 5),
            bandpass_low     = det.get("bandpass_low_hz", 0.1),
            bandpass_high    = det.get("bandpass_high_hz", 2.0),
            sample_rate      = det.get("sample_rate_hz", 100.0),
            hampel_window    = det.get("hampel_window", 5),
            hampel_sigma     = det.get("hampel_sigma", 3.0),
        )
        self.threshold   = det.get("threshold", 15.0)
        self.cooldown    = det.get("cooldown_frames", 30)
        self._since      = self.cooldown
        self._on_motion  = on_motion
        self.frame_count = 0
        self.scores: list[float] = []
        self.events: list[dict]  = []
        self._lock = threading.Lock()

    def feed(self, amplitudes: np.ndarray) -> bool:
        with self._lock:
            self.frame_count += 1
            self._since += 1
            score = self._pipeline.push(amplitudes) or 0.0
            self.scores.append(score)
            detected = score > self.threshold and self._since >= self.cooldown
            if detected:
                self._since = 0
                event = {"timestamp": datetime.now().isoformat(),
                         "frame": self.frame_count, "score": round(score, 2)}
                self.events.append(event)
                if self._on_motion:
                    threading.Thread(target=self._on_motion,
                                     args=(event,), daemon=True).start()
            return detected

    def status(self) -> dict:
        with self._lock:
            score = self.scores[-1] if self.scores else 0.0
            return {"frame": self.frame_count, "score": round(score, 2),
                    "threshold": self.threshold, "motion": score > self.threshold,
                    "events_total": len(self.events),
                    "last_event": self.events[-1] if self.events else None}


# ── Live-Modus ────────────────────────────────────────────────────────────────

def live_mode(cfg):
    notifier = OpchatNotifier(cfg)

    def on_motion(event):
        logging.warning(f"BEWEGUNG | Score: {event['score']} | {event['timestamp']}")
        notifier.notify(event)

    detector = MotionDetector(cfg, on_motion=on_motion)
    port = int(os.environ.get("CSI_PORT", CSI_UDP_PORT))
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", port))
    sock.settimeout(1.0)
    logging.info(f"Motion Detector | Port {port} | Schwelle: {detector.threshold}")

    running = [True]
    signal.signal(signal.SIGINT,  lambda s, f: running.__setitem__(0, False))
    signal.signal(signal.SIGTERM, lambda s, f: running.__setitem__(0, False))

    try:
        while running[0]:
            try: data, _ = sock.recvfrom(4096)
            except socket.timeout: continue
            frame = parse_frame(data)
            if frame is None: continue
            detector.feed(np.array(frame.amplitudes))
            if detector.frame_count % 25 == 0:
                s = detector.status()
                status = "BEWEGUNG" if s["motion"] else "Ruhig   "
                print(f"\r[{s['frame']:6d}] Score: {s['score']:7.2f} | "
                      f"Ereignisse: {s['events_total']:3d} | {status}",
                      end="", flush=True)
    finally:
        sock.close()
        logging.info(f"Beendet. {len(detector.events)} Ereignisse.")
    return detector


# ── Offline-Analyse ──────────────────────────────────────────────────────────

def offline_mode(input_path, cfg, plot):
    logging.info(f"Analysiere: {input_path}")
    timestamps, amps_list = [], []
    with open(input_path, newline="") as f:
        for row in csv.DictReader(f):
            try:
                n = int(row["num_subcarriers"])
                amps_list.append(np.array([float(row.get(f"amp_{i}", 0)) for i in range(n)]))
                timestamps.append(float(row["timestamp"]))
            except (KeyError, ValueError): continue
    if not amps_list: logging.error("Keine Daten."); return
    logging.info(f"{len(amps_list)} Frames, {amps_list[0].shape[0]} Subcarrier")

    detector = MotionDetector(cfg)
    motion_frames = [i for i, a in enumerate(amps_list) if detector.feed(a)]

    print(f"\n=== Ergebnis ===")
    print(f"Bewegungsereignisse: {len(detector.events)}")
    for e in detector.events:
        print(f"  Frame {e['frame']:5d} | Score {e['score']:7.2f} | {e['timestamp']}")
    if plot: _plot(timestamps, detector.scores, motion_frames, detector.threshold)


def _plot(timestamps, scores, motion_frames, threshold):
    try: import matplotlib.pyplot as plt
    except ImportError: print("[!] pip3 install matplotlib"); return
    t = np.array(timestamps) - timestamps[0]
    fig, ax = plt.subplots(figsize=(14, 4))
    ax.fill_between(t[:len(scores)], scores, alpha=0.25, color="steelblue")
    ax.plot(t[:len(scores)], scores, color="steelblue", lw=0.9, label="Score")
    ax.axhline(threshold, color="red", ls="--", label=f"Schwelle ({threshold})")
    for mf in motion_frames:
        if mf < len(t): ax.axvline(t[mf], color="orange", alpha=0.7, lw=1.2)
    ax.set_xlabel("Zeit (s)"); ax.set_ylabel("Bewegungs-Score")
    ax.legend(); ax.grid(True, alpha=0.3); plt.tight_layout(); plt.show()


def main():
    parser = argparse.ArgumentParser(description="WiFi Motion Detection via Nexmon CSI")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--live",  action="store_true")
    mode.add_argument("--input", help="CSV-Datei")
    parser.add_argument("--config",    default="config.json")
    parser.add_argument("--threshold", type=float)
    parser.add_argument("--plot",      action="store_true")
    args = parser.parse_args()

    cfg = load_config(args.config)
    if args.threshold:
        cfg.setdefault("detection", {})["threshold"] = args.threshold
    setup_logging(cfg)

    if args.live:       live_mode(cfg)
    elif args.input:    offline_mode(args.input, cfg, args.plot)
    else:               parser.print_help(); sys.exit(1)

if __name__ == "__main__":
    main()
