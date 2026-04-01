#!/bin/bash
# Auto-Update Service für nexmon-csi-wifi-motion
# Prüft alle 5 Minuten auf neue Commits und zieht Updates automatisch

REPO_DIR="/opt/nexmon-csi-wifi-motion"
LOG="/var/log/nexmon-autoupdate.log"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG"; }

if [ ! -d "$REPO_DIR/.git" ]; then
    log "Repo nicht gefunden: $REPO_DIR"
    exit 1
fi

cd "$REPO_DIR"

LOCAL=$(git rev-parse HEAD)
git fetch origin main -q 2>>"$LOG"
REMOTE=$(git rev-parse origin/main)

if [ "$LOCAL" != "$REMOTE" ]; then
    log "Update gefunden: $LOCAL -> $REMOTE"
    git pull origin main -q 2>>"$LOG"
    log "Update erfolgreich."

    # Motion Detector neu starten falls er läuft
    if systemctl is-active --quiet nexmon-motion 2>/dev/null; then
        systemctl restart nexmon-motion
        log "nexmon-motion Service neu gestartet."
    fi
else
    log "Kein Update."
fi
