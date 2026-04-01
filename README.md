# nexmon-csi-wifi-motion

Nexmon CSI Setup und WiFi Motion Detection für Raspberry Pi 4 (BCM43455).

## Voraussetzungen

- Raspberry Pi 4 mit Raspberry Pi OS **32-bit** (Buster/Bullseye)
- Kernel 5.10 (empfohlen: `rpi-update` auf kompatible Version)
- Root-Zugriff

## Schnellstart

### 1. Repository klonen

```bash
git clone https://github.com/habaal/nexmon-csi-wifi-motion.git
cd nexmon-csi-wifi-motion
```

### 2. Nexmon CSI installieren

```bash
sudo bash scripts/setup_nexmon.sh install
sudo reboot
```

### 3. Bewegungserkennung starten

```bash
sudo bash scripts/start_csi.sh --channel 6 --bw 80
```

## Einzelne Skripte

| Skript | Beschreibung |
|--------|-------------|
| `scripts/setup_nexmon.sh` | Installiert Nexmon, CSI-Patch, Kernel-Modul |
| `scripts/start_csi.sh` | Startet Monitor-Modus + Collector + Detektor |
| `src/collect_csi.py` | Empfängt CSI-UDP-Pakete, speichert als CSV |
| `src/motion_detection.py` | Erkennt Bewegungen (Live + Offline) |

## Offline-Analyse

```bash
# CSI-Daten aufzeichnen (60 Sekunden)
sudo python3 src/collect_csi.py --duration 60 --output data/test.csv

# Analyse mit Plot
python3 src/motion_detection.py --input data/test.csv --plot
```

## Firmware wiederherstellen

```bash
sudo bash scripts/setup_nexmon.sh restore
```

## Quellen

- [Nexmon](https://github.com/seemoo-lab/nexmon)
- [Nexmon CSI](https://github.com/seemoo-lab/nexmon_csi)
