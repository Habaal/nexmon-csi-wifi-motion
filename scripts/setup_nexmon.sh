#!/bin/bash
# =============================================================================
# Nexmon CSI Setup Script for Raspberry Pi 4 (BCM43455)
# WiFi Motion Detection - Maturaprojekt
# =============================================================================
# Prerequisites: Raspberry Pi OS (32-bit) mit Kernel 5.10
# Run as root: sudo bash setup_nexmon.sh
# =============================================================================

set -e

NEXMON_DIR="/opt/nexmon"
NEXMON_CSI_DIR="/opt/nexmon_csi"
LOG_FILE="/var/log/nexmon_setup.log"
WIFI_IFACE="wlan0"
KERNEL_VERSION=$(uname -r)

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "${GREEN}[INFO]${NC} $1" | tee -a "$LOG_FILE"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1" | tee -a "$LOG_FILE"; }
error(){ echo -e "${RED}[ERROR]${NC} $1" | tee -a "$LOG_FILE"; exit 1; }

check_root() {
    [ "$EUID" -eq 0 ] || error "Muss als root ausgeführt werden: sudo bash $0"
}

check_rpi4() {
    grep -q "Raspberry Pi 4" /proc/device-tree/model 2>/dev/null \
        && log "Raspberry Pi 4 erkannt." \
        || warn "Kein RPi4 erkannt – fortfahren auf eigenes Risiko."
}

check_kernel() {
    log "Kernel: $KERNEL_VERSION"
    KMAJOR=$(echo "$KERNEL_VERSION" | cut -d. -f1)
    [ "$KMAJOR" -ge 5 ] || error "Kernel >= 5.x benötigt. Aktuell: $KERNEL_VERSION"
    log "Kernel-Version kompatibel."
}

install_dependencies() {
    log "Installiere Abhängigkeiten..."
    apt-get update -qq
    apt-get install -y \
        git gawk qpdf flex bison make automake autoconf libtool \
        libssl-dev libgmp3-dev tcpdump \
        python3 python3-pip python3-venv \
        build-essential raspberrypi-kernel-headers \
        libpcap-dev pkg-config \
        2>&1 | tee -a "$LOG_FILE"
    log "Abhängigkeiten installiert."
}

install_python_deps() {
    log "Installiere Python-Pakete..."
    pip3 install --quiet numpy scipy matplotlib scapy 2>&1 | tee -a "$LOG_FILE"
    log "Python-Pakete installiert."
}

clone_nexmon() {
    [ -d "$NEXMON_DIR" ] && { warn "Nexmon bereits vorhanden. Überspringe."; return; }
    log "Klone Nexmon..."
    git clone https://github.com/seemoo-lab/nexmon.git "$NEXMON_DIR" 2>&1 | tee -a "$LOG_FILE"
}

clone_nexmon_csi() {
    [ -d "$NEXMON_CSI_DIR" ] && { warn "Nexmon CSI bereits vorhanden. Überspringe."; return; }
    log "Klone Nexmon CSI..."
    git clone https://github.com/seemoo-lab/nexmon_csi.git "$NEXMON_CSI_DIR" 2>&1 | tee -a "$LOG_FILE"
}

build_nexmon() {
    log "Baue Nexmon-Umgebung..."
    cd "$NEXMON_DIR"
    source ./setup_env.sh
    make -C buildtools 2>&1 | tee -a "$LOG_FILE"
    make -C firmwares/bcm43455/7_45_206_p33_usi/nexmon 2>&1 | tee -a "$LOG_FILE"
    log "Nexmon Build abgeschlossen."
}

build_nexmon_csi() {
    log "Baue Nexmon CSI Patch..."
    cd "$NEXMON_CSI_DIR"
    NEXMON_ROOT="$NEXMON_DIR" make 2>&1 | tee -a "$LOG_FILE"
    log "Nexmon CSI Build abgeschlossen."
}

install_nexmon_csi() {
    log "Installiere Nexmon CSI Firmware..."
    cd "$NEXMON_CSI_DIR"

    FIRMWARE_PATH="/lib/firmware/brcm/brcmfmac43455-sdio.bin"
    KERNEL_MOD_DIR="/lib/modules/${KERNEL_VERSION}/kernel/drivers/net/wireless/broadcom/brcm80211/brcmfmac"

    # Backup
    [ -f "${FIRMWARE_PATH}.orig" ] || cp "$FIRMWARE_PATH" "${FIRMWARE_PATH}.orig"
    log "Original-Firmware gesichert."

    # Gepatchte Firmware kopieren
    install -m 644 brcmfmac_5.10.y-nexmon/brcmfmac43455-sdio.bin "$FIRMWARE_PATH"
    log "Gepatchte Firmware installiert."

    # Kernel-Modul
    if [ -f "brcmfmac_5.10.y-nexmon/brcmfmac.ko" ]; then
        install -m 644 brcmfmac_5.10.y-nexmon/brcmfmac.ko "$KERNEL_MOD_DIR/"
        depmod -a
        log "Kernel-Modul installiert."
    fi

    # nexutil installieren
    log "Installiere nexutil..."
    cd "$NEXMON_DIR/utilities/nexutil"
    make && make install 2>&1 | tee -a "$LOG_FILE"
    log "nexutil installiert."
}

configure_monitor_service() {
    log "Erstelle systemd-Service für Monitor-Modus..."

    cat > /etc/systemd/system/nexmon-monitor.service <<EOF
[Unit]
Description=Nexmon CSI Monitor Mode
After=network.target

[Service]
Type=oneshot
ExecStart=/usr/local/bin/nexmon-start.sh
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF

    cat > /usr/local/bin/nexmon-start.sh <<'SCRIPT'
#!/bin/bash
IFACE="wlan0"
CHANNEL=6
BW=80

ip link set "$IFACE" down
iw dev "$IFACE" set type monitor
ip link set "$IFACE" up
iw dev "$IFACE" set channel $CHANNEL ${BW}MHz 2>/dev/null || \
    iw dev "$IFACE" set channel $CHANNEL

nexutil -I"$IFACE" -s500 -b -l 34 \
    -v "$(python3 -c "import struct,sys; sys.stdout.buffer.write(struct.pack('<HBBi',${CHANNEL},0xFF,${BW},0))")"

echo "Nexmon CSI aktiv: Kanal $CHANNEL, ${BW} MHz"
SCRIPT

    chmod +x /usr/local/bin/nexmon-start.sh
    systemctl daemon-reload
    systemctl enable nexmon-monitor.service
    log "systemd-Service konfiguriert."
}

configure_autoupdate() {
    log "Richte Auto-Update-Service ein (alle 5 Minuten)..."

    install -m 755 "$(dirname "$0")/autoupdate.sh" /usr/local/bin/nexmon-autoupdate.sh

    # systemd timer statt cron
    cat > /etc/systemd/system/nexmon-autoupdate.service <<EOF
[Unit]
Description=Nexmon CSI Auto-Update
After=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/bin/nexmon-autoupdate.sh
EOF

    cat > /etc/systemd/system/nexmon-autoupdate.timer <<EOF
[Unit]
Description=Nexmon CSI Auto-Update alle 5 Minuten

[Timer]
OnBootSec=2min
OnUnitActiveSec=5min

[Install]
WantedBy=timers.target
EOF

    systemctl daemon-reload
    systemctl enable --now nexmon-autoupdate.timer
    log "Auto-Update aktiv. Logs: /var/log/nexmon-autoupdate.log"
}

restore_firmware() {
    FW="/lib/firmware/brcm/brcmfmac43455-sdio.bin"
    [ -f "${FW}.orig" ] || error "Kein Firmware-Backup gefunden!"
    cp "${FW}.orig" "$FW"
    log "Original-Firmware wiederhergestellt."
}

print_summary() {
    echo ""
    echo "============================================"
    echo "  Nexmon CSI Setup abgeschlossen!"
    echo "============================================"
    echo ""
    echo "  1. Neu starten:        sudo reboot"
    echo "  2. Monitor prüfen:     iw dev wlan0 info"
    echo "  3. CSI sammeln:        sudo python3 src/collect_csi.py"
    echo "  4. Motion Detection:   python3 src/motion_detection.py --live"
    echo ""
    echo "  Logs: $LOG_FILE"
    echo "  Auto-Update: alle 5 Min via systemd timer"
}

case "${1:-install}" in
    install)
        check_root; check_rpi4; check_kernel
        install_dependencies; install_python_deps
        clone_nexmon; clone_nexmon_csi
        build_nexmon; build_nexmon_csi
        install_nexmon_csi; configure_monitor_service
        configure_autoupdate
        print_summary
        ;;
    restore)
        check_root; restore_firmware ;;
    *)
        echo "Verwendung: $0 [install|restore]"; exit 1 ;;
esac

install_python_requirements() {
    log "Installiere Python-Pakete..."
    pip3 install --quiet \
        numpy scipy matplotlib \
        flask flask-socketio \
        scapy \
        2>&1 | tee -a "$LOG_FILE"
    log "Python-Pakete installiert."
}
