"""
CSI Data Collector für Nexmon CSI (Raspberry Pi 4 / BCM43455)
Empfängt UDP-Pakete vom Nexmon-Treiber und speichert CSI-Daten als CSV.

Verwendung:
    sudo python3 collect_csi.py [--output data.csv] [--duration 60]
"""

import socket, struct, argparse, csv, time, signal, os
from datetime import datetime
from csi_parser import parse as parse_frame

CSI_UDP_PORT = 5500


def csv_header(n_sub: int) -> list[str]:
    return ["timestamp", "rssi", "src_mac", "seq_num", "num_subcarriers"] + \
           [f"amp_{i}" for i in range(n_sub)]


def collect(output_path: str, duration: float | None, verbose: bool):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", CSI_UDP_PORT))
    sock.settimeout(1.0)

    print(f"[*] CSI-Collector | Port {CSI_UDP_PORT} | Ausgabe: {output_path}")
    if duration: print(f"[*] Dauer: {duration:.0f}s")
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
            try: data, _ = sock.recvfrom(4096)
            except socket.timeout: continue

            frame = parse_frame(data)
            if frame is None:
                error_count += 1
                continue

            if not header_written:
                writer.writerow(csv_header(frame.num_subcarriers))
                f.flush()
                header_written = True

            row = [frame.timestamp, frame.rssi, frame.src_mac,
                   frame.seq_num, frame.num_subcarriers] + frame.amplitudes
            writer.writerow(row)
            frame_count += 1

            if frame_count % 100 == 0:
                f.flush()

            if verbose or frame_count % 50 == 0:
                elapsed = time.time() - start
                fps = frame_count / elapsed if elapsed > 0 else 0
                print(f"\r[{elapsed:6.1f}s] Frames: {frame_count:6d} | "
                      f"FPS: {fps:5.1f} | RSSI: {frame.rssi:4d} dBm",
                      end="", flush=True)

    sock.close()
    print(f"\n[+] {frame_count} Frames gespeichert, {error_count} ungültig.")


def main():
    parser = argparse.ArgumentParser(description="Nexmon CSI Collector")
    parser.add_argument("--output", default=f"csi_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv")
    parser.add_argument("--duration", type=float, default=None)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    if os.geteuid() != 0:
        print("[!] Warnung: Root-Rechte empfohlen.")
    collect(args.output, args.duration, args.verbose)

if __name__ == "__main__":
    main()
