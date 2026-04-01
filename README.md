# nexmon-csi-wifi-motion

WiFi Motion Detection für Raspberry Pi 4 via Nexmon CSI (BCM43455).

## Features

- **CSI-Erfassung** – UDP-Pakete vom Nexmon-Treiber empfangen & speichern
- **Signalverarbeitung** – Hampel-Filter + Butterworth-Bandpass + PCA
- **Bewegungserkennung** – Echtzeit-Detection mit konfigurierbarem Schwellwert
- **Web-Dashboard** – Live-Score-Graph & Ereignisliste im Browser
- **REST API** – `/api/status`, `/api/events`
- **OPCHAT-Benachrichtigung** – Push-Nachricht bei Bewegung
- **Auto-Update** – Pi zieht automatisch neue Versionen von GitHub

## Schnellstart

### Installation (einmalig)
```bash
git clone https://github.com/Habaal/nexmon-csi-wifi-motion.git /opt/nexmon-csi-wifi-motion
cd /opt/nexmon-csi-wifi-motion
sudo bash scripts/setup_nexmon.sh install
sudo reboot
```

### Starten
```bash
# Mit Web-Dashboard (empfohlen)
sudo bash scripts/start_csi.sh --channel 6 --bw 80

# Nur Bewegungserkennung (kein Dashboard)
sudo bash scripts/start_csi.sh --no-dashboard
```

Dashboard öffnen: **http://\<Pi-IP\>:5000**

## Konfiguration

Alle Parameter in `config.json`:

| Parameter | Standard | Beschreibung |
|-----------|---------|--------------|
| `wifi.channel` | 6 | WiFi-Kanal des Ziel-APs |
| `wifi.bandwidth` | 80 | Bandbreite in MHz (20/40/80) |
| `detection.threshold` | 15.0 | Erkennungsschwelle |
| `detection.baseline_window` | 100 | Frames für Ruhe-Referenz |
| `detection.bandpass_low_hz` | 0.1 | Untergrenze Bandpassfilter |
| `detection.bandpass_high_hz` | 2.0 | Obergrenze (menschl. Bewegung) |
| `notifications.opchat_enabled` | false | OPCHAT-Alarm aktivieren |
| `server.port` | 5000 | Dashboard-Port |

## Offline-Analyse

```bash
# CSI aufzeichnen
sudo python3 src/collect_csi.py --duration 60 --output data/test.csv

# Analyse mit Plot
python3 src/motion_detection.py --input data/test.csv --plot
```

## API

```bash
curl http://<Pi-IP>:5000/api/status   # Aktueller Zustand
curl http://<Pi-IP>:5000/api/events   # Letzte 50 Ereignisse
```

## Signalverarbeitungs-Pipeline

```
CSI-Frame → Amplituden
    → Hampel-Identifier (Ausreißer entfernen)
    → Butterworth-Bandpass (0.1–2 Hz)
    → PCA (5 Hauptkomponenten)
    → L2-Score vs. Baseline
    → Score > Schwelle → Bewegung erkannt
```

## Quellen

- [Nexmon](https://github.com/seemoo-lab/nexmon)
- [Nexmon CSI](https://github.com/seemoo-lab/nexmon_csi)
