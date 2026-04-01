#!/usr/bin/env python3
"""
CSI Data Collector für Nexmon CSI (Raspberry Pi 4 / BCM43455)
Empfängt UDP-Pakete vom Nexmon-Treiber und speichert rohe CSI-Daten als CSV.

Verwendung:
    sudo python3 collect_csi.py [--output data.csv] [--duration 60]
"""

import socket
import struct
import argparse
import csv
import time
import signal
import sys
import os
from datetime import datetime

CSI_UDP_PORT = 5500
NEXMON_MAGIC = 0x11111111
HEADER_SIZE = 19      # Bytes vor CSI-Payload
MAX_SUBCARRIERS = 256


class NexmonCSIFrame:
    """Geparster Nexmon CSI UDP-Frame."""

    def __init__(self, raw: bytes):
        if len(raw) < HEADER_SIZE:
            raise ValueError(f"Frame zu kurz: {len(raw)} Bytes")

        magic, rssi_raw, fc = struct.unpack_from("<IBB", raw, 0)
        if magic != NEXMON_MAGIC:
            raise ValueError(f"Ungültiger Magic: 0x{magic:08x}")

        self.rssi: int = struct.unpack("b", bytes([rssi_raw]))[0]
        self.frame_control: int = fc
        self.src_mac: str = ":".join(f"{b:02x}" for b in raw[6:12])
        self.seq_num: int = struct.unpack_from("<H", raw, 12)[0]
        self.channel_spec: int = struct.unpack_from("<H", raw, 15)[0]
        self.timestamp: float = time.time()

        payload = raw[HEADER_SIZE:]
        n = min(len(payload) // 4, MAX_SUBCARRIERS)
        self.csi = [
            complex(*struct.unpack_from("<hh", payload, i * 4))
            for i in range(n)
        ]
        self.num_subcarriers = len(self.csi)

    @property
    def amplitudes(self) -> list[float]:
        return [abs(c) for c in self.csi]

    def to_csv_row(self) -> list:
        return [
            self.timestamp, self.rssi, self.src_mac,
            self.seq_num, self.num_subcarriers,
        ] + self.amplitudes


def csv_header(n_sub: int) -> list[str]:
    return ["timestamp", "rssi", "src_mac", "seq_num", "num_subcarriers"] + \
           [f"amp_{i}" for i in range(n_sub)]


def collect(output_path: str, duration: float | None, verbose: bool):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", CSI_UDP_PORT))
    sock.settimeout(1.0)

    print(f"[*] CSI-Collector auf UDP-Port {CSI_UDP_PORT}")
    print(f"[*] Ausgabe: {output_path}")
    if duration:
        print(f"[*] Dauer: {duration:.0f}s")
    print("[*] Strg+C zum Beenden\n")

    running = [True]
    signal.signal(signal.SIGINT,  lambda s, f: running.__setitem__(0, False))
    signal.signal(signal.SIGTERM, lambda s, f: running.__setitem__(0, False))

    frame_count = error_count = 0
    header_written = False
    start = time.time()

    with open(output_path, "w", newline="") as f:
        writer = csv.writer(f)
        while running[0]:
            if duration and (time.time() - start) >= duration:
                print("\n[*] Aufnahmedauer erreicht.")
                break
            try:
                data, _ = sock.recvfrom(4096)
            except socket.timeout:
                continue
            try:
                frame = NexmonCSIFrame(data)
            except (ValueError, struct.error) as e:
                error_count += 1
                if verbose:
                    print(f"[!] {e}")
                continue

            if not header_written:
                writer.writerow(csv_header(frame.num_subcarriers))
                f.flush()
                header_written = True

            writer.writerow(frame.to_csv_row())
            frame_count += 1

            if frame_count % 100 == 0:
                f.flush()

            if verbose or frame_count % 50 == 0:
                elapsed = time.time() - start
                fps = frame_count / elapsed if elapsed > 0 else 0
                print(
                    f"\r[{elapsed:6.1f}s] Frames: {frame_count:6d} | "
                    f"FPS: {fps:5.1f} | RSSI: {frame.rssi:4d} dBm",
                    end="", flush=True,
                )

    sock.close()
    print(f"\n[+] {frame_count} Frames gespeichert, {error_count} Fehler.")


def main():
    parser = argparse.ArgumentParser(description="Nexmon CSI Collector")
    parser.add_argument(
        "--output",
        default=f"csi_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
    )
    parser.add_argument("--duration", type=float, default=None)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    if os.geteuid() != 0:
        print("[!] Warnung: Root-Rechte empfohlen.")

    collect(args.output, args.duration, args.verbose)


if __name__ == "__main__":
    main()
