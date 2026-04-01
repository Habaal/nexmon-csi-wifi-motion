#!/bin/bash
# =============================================================================
# CSI Collection + Motion Detection starten
# Aktiviert Monitor-Modus, sammelt CSI-Daten und startet Bewegungserkennung.
# Verwendung: sudo bash start_csi.sh [--channel 6] [--bw 80]
# =============================================================================

set -e

WIFI_IFACE="wlan0"
CHANNEL=6
BW=80   # MHz: 20 / 40 / 80
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DATA_DIR="$REPO_DIR/data"
SRC_DIR="$REPO_DIR/src"

RED='\033[0;31m'; GREEN='\033[0;32m'; NC='\033[0m'

[ "$EUID" -eq 0 ] || { echo -e "${RED}[!] sudo benötigt: sudo bash $0${NC}"; exit 1; }

# Argumente parsen
while [[ $# -gt 0 ]]; do
    case "$1" in
        --channel) CHANNEL="$2"; shift 2 ;;
        --bw)      BW="$2";      shift 2 ;;
        *) echo "Unbekannte Option: $1"; exit 1 ;;
    esac
done

setup_monitor() {
    echo -e "${GREEN}[*] Monitor-Modus: $WIFI_IFACE, Kanal $CHANNEL, ${BW} MHz${NC}"
    ip link set "$WIFI_IFACE" down
    iw dev "$WIFI_IFACE" set type monitor
    ip link set "$WIFI_IFACE" up
    iw dev "$WIFI_IFACE" set channel "$CHANNEL" "${BW}MHz" 2>/dev/null || \
        iw dev "$WIFI_IFACE" set channel "$CHANNEL"

    nexutil -I"$WIFI_IFACE" -s500 -b -l 34 \
        -v "$(python3 -c "import struct,sys; sys.stdout.buffer.write(struct.pack('<HBBi',$CHANNEL,0xFF,$BW,0))")" \
        2>/dev/null && echo -e "${GREEN}[+] CSI via nexutil aktiviert.${NC}" \
                    || echo -e "${RED}[!] nexutil fehlgeschlagen – CSI evtl. inaktiv.${NC}"
}

start_collector() {
    mkdir -p "$DATA_DIR"
    OUTFILE="$DATA_DIR/csi_$(date +%Y%m%d_%H%M%S).csv"
    echo -e "${GREEN}[*] Collector → $OUTFILE${NC}"
    python3 "$SRC_DIR/collect_csi.py" --output "$OUTFILE" &
    echo $! > /tmp/csi_collector.pid
}

stop_all() {
    echo -e "\n${GREEN}[*] Stoppe...${NC}"
    [ -f /tmp/csi_collector.pid ] && kill "$(cat /tmp/csi_collector.pid)" 2>/dev/null; rm -f /tmp/csi_collector.pid
    ip link set "$WIFI_IFACE" down
    iw dev "$WIFI_IFACE" set type managed
    ip link set "$WIFI_IFACE" up
    echo -e "${GREEN}[+] Fertig.${NC}"
}

trap stop_all INT TERM

setup_monitor
start_collector

echo -e "${GREEN}[*] Motion Detector gestartet (Strg+C zum Beenden)${NC}"
python3 "$SRC_DIR/motion_detection.py" --live

wait
