#!/bin/bash
# Startet Monitor-Modus + CSI-Dashboard + Collector
# Verwendung: sudo bash start_csi.sh [--channel 6] [--bw 80] [--no-dashboard]

set -e
WIFI_IFACE="wlan0"
CHANNEL=6
BW=80
DASHBOARD=true
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DATA_DIR="$REPO_DIR/data"
SRC_DIR="$REPO_DIR/src"
GREEN='\033[0;32m'; RED='\033[0;31m'; NC='\033[0m'

[ "$EUID" -eq 0 ] || { echo -e "${RED}[!] sudo benötigt${NC}"; exit 1; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --channel)      CHANNEL="$2"; shift 2 ;;
        --bw)           BW="$2";      shift 2 ;;
        --no-dashboard) DASHBOARD=false; shift ;;
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
        2>/dev/null && echo -e "${GREEN}[+] CSI aktiv${NC}" \
                    || echo -e "${RED}[!] nexutil fehlgeschlagen${NC}"
}

stop_all() {
    echo -e "\n${GREEN}[*] Stoppe...${NC}"
    for PID_FILE in /tmp/csi_collector.pid /tmp/csi_dashboard.pid; do
        [ -f "$PID_FILE" ] && kill "$(cat $PID_FILE)" 2>/dev/null; rm -f "$PID_FILE"
    done
    ip link set "$WIFI_IFACE" down
    iw dev "$WIFI_IFACE" set type managed
    ip link set "$WIFI_IFACE" up
    echo -e "${GREEN}[+] Fertig.${NC}"
}

trap stop_all INT TERM

setup_monitor
mkdir -p "$DATA_DIR"

# Collector starten
OUTFILE="$DATA_DIR/csi_$(date +%Y%m%d_%H%M%S).csv"
python3 "$SRC_DIR/collect_csi.py" --output "$OUTFILE" &
echo $! > /tmp/csi_collector.pid
echo -e "${GREEN}[*] Collector gestartet → $OUTFILE${NC}"

# Dashboard oder nur Detector
if $DASHBOARD; then
    PI_IP=$(hostname -I | awk '{print $1}')
    echo -e "${GREEN}[*] Dashboard: http://$PI_IP:5000${NC}"
    python3 "$SRC_DIR/dashboard.py" &
    echo $! > /tmp/csi_dashboard.pid
    wait
else
    python3 "$SRC_DIR/motion_detection.py" --live
fi
