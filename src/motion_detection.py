#!/usr/bin/env python3
"""
WiFi Motion Detection via Nexmon CSI
Erkennt Bewegungen anhand von Veränderungen in CSI-Amplituden (BCM43455).

Algorithmus:
  1. CSI-Amplituden (Subcarrier-Magnituden) einlesen
  2. Gleitender Mittelwert über Baseline-Fenster als Referenz
  3. Aktuelles Fenster per PCA projizieren
  4. L2-Abstand zur Baseline > Schwellwert → Bewegung erkannt

Verwendung:
  Echtzeit:    sudo python3 motion_detection.py --live
  Offline:     python3 motion_detection.py --input csi_data.csv --plot
"""

import argparse
import csv
import socket
import struct
import time
import threading
import sys
from collections import deque
from datetime import datetime

import numpy as np

# ── Standardkonfiguration ─────────────────────────────────────────────────────
CSI_UDP_PORT     = 5500
NEXMON_MAGIC     = 0x11111111
HEADER_SIZE      = 19
BASELINE_WINDOW  = 100    # Frames für Ruhe-Referenz
DETECTION_WINDOW = 10     # Frames für aktuellen Zustand
MOTION_THRESHOLD = 15.0   # Erkennungsschwelle (CSI-Amplituden-Einheiten)
COOLDOWN_FRAMES  = 30     # Min. Abstand zwischen zwei Ereignissen
PCA_COMPONENTS   = 5      # Hauptkomponenten für PCA-Projektion
# ─────────────────────────────────────────────────────────────────────────────


class CSIBuffer:
    def __init__(self, maxlen: int):
        self._buf: deque[np.ndarray] = deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def append(self, amp: np.ndarray):
        with self._lock:
            self._buf.append(amp)

    def matrix(self) -> np.ndarray | None:
        with self._lock:
            return np.array(self._buf) if self._buf else None

    def __len__(self):
        with self._lock:
            return len(self._buf)

    @property
    def maxlen(self):
        return self._buf.maxlen


def parse_frame(raw: bytes) -> np.ndarray | None:
    if len(raw) < HEADER_SIZE + 4:
        return None
    if struct.unpack_from("<I", raw, 0)[0] != NEXMON_MAGIC:
        return None
    payload = raw[HEADER_SIZE:]
    n = len(payload) // 4
    iq = np.frombuffer(payload[: n * 4], dtype="<i2").reshape(n, 2).astype(float)
    return np.hypot(iq[:, 0], iq[:, 1])


class MotionDetector:
    def __init__(
        self,
        threshold: float = MOTION_THRESHOLD,
        baseline_window: int = BASELINE_WINDOW,
        detection_window: int = DETECTION_WINDOW,
        cooldown: int = COOLDOWN_FRAMES,
        on_motion=None,
    ):
        self.threshold = threshold
        self._bl = CSIBuffer(maxlen=baseline_window)
        self._det = CSIBuffer(maxlen=detection_window)
        self._cooldown = cooldown
        self._since_event = cooldown
        self._on_motion = on_motion
        self.events: list[dict] = []
        self.frame_count = 0
        self.scores: list[float] = []

    def feed(self, amp: np.ndarray) -> bool:
        self.frame_count += 1
        self._bl.append(amp)
        self._det.append(amp)
        self._since_event += 1

        if len(self._bl) < self._bl.maxlen // 2:
            self.scores.append(0.0)
            return False

        bl_mat  = self._bl.matrix()
        det_mat = self._det.matrix()
        bl_mean = bl_mat.mean(axis=0)

        n_comp = min(PCA_COMPONENTS, bl_mat.shape[1])
        if n_comp >= 2:
            centered = bl_mat - bl_mean
            cov = np.cov(centered.T)
            _, vecs = np.linalg.eigh(cov)
            top = vecs[:, np.argsort(np.linalg.eigh(cov)[0])[::-1][:n_comp]]
            bl_proj  = (bl_mat  - bl_mean) @ top
            det_proj = (det_mat - bl_mean) @ top
        else:
            bl_proj  = bl_mat  - bl_mean
            det_proj = det_mat - bl_mean

        score = float(np.linalg.norm(det_proj.mean(0) - bl_proj.mean(0)))
        self.scores.append(score)

        if score > self.threshold and self._since_event >= self._cooldown:
            self._since_event = 0
            event = {
                "timestamp": datetime.now().isoformat(),
                "frame": self.frame_count,
                "score": score,
            }
            self.events.append(event)
            if self._on_motion:
                self._on_motion(event)
            return True
        return False


# ── Live-Modus ────────────────────────────────────────────────────────────────

def live_mode(threshold: float, verbose: bool):
    def on_motion(e):
        print(f"\n*** BEWEGUNG ERKANNT | Score: {e['score']:.1f} | {e['timestamp']} ***")

    detector = MotionDetector(threshold=threshold, on_motion=on_motion)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", CSI_UDP_PORT))
    sock.settimeout(1.0)

    print(f"[*] Live Motion Detection | Port {CSI_UDP_PORT} | Schwelle: {threshold}")
    print("[*] Strg+C zum Beenden\n")

    running = [True]
    import signal
    signal.signal(signal.SIGINT,  lambda s, f: running.__setitem__(0, False))
    signal.signal(signal.SIGTERM, lambda s, f: running.__setitem__(0, False))

    try:
        while running[0]:
            try:
                data, _ = sock.recvfrom(4096)
            except socket.timeout:
                continue
            amp = parse_frame(data)
            if amp is None:
                continue
            detector.feed(amp)
            if detector.frame_count % 20 == 0:
                score = detector.scores[-1] if detector.scores else 0.0
                bl_fill = len(detector._bl)
                status = "BEWEGUNG" if score > threshold else "Ruhig   "
                print(
                    f"\r[Frame {detector.frame_count:6d}] "
                    f"Baseline: {bl_fill:3d}/{detector._bl.maxlen} | "
                    f"Score: {score:7.2f} | "
                    f"Ereignisse: {len(detector.events):3d} | {status}",
                    end="", flush=True,
                )
    finally:
        sock.close()
        print(f"\n[+] Beendet. {len(detector.events)} Ereignisse erkannt.")


# ── Offline-Analyse ──────────────────────────────────────────────────────────

def offline_mode(input_path: str, threshold: float, plot: bool):
    print(f"[*] Analysiere: {input_path}")
    timestamps, amps_list = [], []

    with open(input_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                n = int(row["num_subcarriers"])
                amp = np.array([float(row.get(f"amp_{i}", 0)) for i in range(n)])
                timestamps.append(float(row["timestamp"]))
                amps_list.append(amp)
            except (KeyError, ValueError):
                continue

    if not amps_list:
        print("[!] Keine gültigen Daten gefunden.")
        return

    print(f"[*] {len(amps_list)} Frames, {amps_list[0].shape[0]} Subcarrier")

    detector = MotionDetector(threshold=threshold)
    motion_frames = []
    for i, amp in enumerate(amps_list):
        if detector.feed(amp):
            motion_frames.append(i)

    print(f"\n[+] Ergebnis:")
    print(f"    Schwellwert:          {threshold}")
    print(f"    Bewegungsereignisse:  {len(detector.events)}")
    for e in detector.events:
        print(f"      Frame {e['frame']:5d} | Score {e['score']:7.2f} | {e['timestamp']}")

    if plot:
        _plot(timestamps, detector.scores, motion_frames, threshold)


def _plot(timestamps, scores, motion_frames, threshold):
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        print("[!] matplotlib fehlt: pip3 install matplotlib")
        return

    t = np.array(timestamps) - timestamps[0]
    fig, ax = plt.subplots(figsize=(12, 4))
    fig.suptitle("WiFi Motion Detection – CSI Score-Verlauf")
    ax.plot(t[: len(scores)], scores, color="steelblue", lw=0.8, label="Score")
    ax.axhline(threshold, color="red", ls="--", label=f"Schwelle ({threshold})")
    for mf in motion_frames:
        if mf < len(t):
            ax.axvline(t[mf], color="orange", alpha=0.5, lw=1)
    ax.set_xlabel("Zeit (s)")
    ax.set_ylabel("Bewegungs-Score")
    ax.legend()
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.show()


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="WiFi Motion Detection via Nexmon CSI")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--live",  action="store_true", help="Echtzeit-Erkennung per UDP")
    mode.add_argument("--input", help="CSV-Datei für Offline-Analyse")
    parser.add_argument("--threshold", type=float, default=MOTION_THRESHOLD)
    parser.add_argument("--plot", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    if args.live:
        live_mode(args.threshold, args.verbose)
    elif args.input:
        offline_mode(args.input, args.threshold, args.plot)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
