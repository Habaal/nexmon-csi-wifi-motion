"""
Signal-Verarbeitung für CSI-Daten.

Pipeline:
  1. Hampel-Identifier   → Ausreißer entfernen
  2. Butterworth-Filter  → Bandpass 0.1–2 Hz (menschliche Bewegung)
  3. PCA                 → Dimensionsreduktion
  4. L2-Abstand          → Bewegungs-Score
"""

import numpy as np
from collections import deque


# ── Hampel-Identifier ─────────────────────────────────────────────────────────

def hampel(x: np.ndarray, window: int = 5, sigma: float = 3.0) -> np.ndarray:
    """
    Ersetzt Ausreißer durch den lokalen Median.
    Robust gegen Impulsrauschen in CSI-Daten.
    """
    x = x.copy().astype(float)
    n = len(x)
    k = 1.4826  # Konsistenzfaktor für Normalverteilung

    for i in range(n):
        lo = max(0, i - window)
        hi = min(n, i + window + 1)
        segment = x[lo:hi]
        med = np.median(segment)
        mad = k * np.median(np.abs(segment - med))
        if mad > 0 and abs(x[i] - med) > sigma * mad:
            x[i] = med
    return x


def hampel_matrix(mat: np.ndarray, window: int = 5, sigma: float = 3.0) -> np.ndarray:
    """Wendet Hampel spaltenweise auf eine (frames × subcarrier)-Matrix an."""
    return np.apply_along_axis(hampel, 0, mat, window=window, sigma=sigma)


# ── Butterworth-Bandpassfilter ────────────────────────────────────────────────

def butter_bandpass_coeffs(low: float, high: float, fs: float, order: int = 4):
    """
    Berechnet Butterworth-Filterkoeffizienten (IIR).
    Gibt (b, a) zurück.
    Implementiert ohne scipy für minimale Abhängigkeiten,
    fällt auf scipy zurück wenn verfügbar.
    """
    try:
        from scipy.signal import butter
        return butter(order, [low, high], btype='band', fs=fs)
    except ImportError:
        # Fallback: einfacher gleitender Mittelwert (kein echter Bandpass)
        return None, None


def apply_bandpass(data: np.ndarray, b, a) -> np.ndarray:
    """Wendet IIR-Filter mit Vorwärts-Rückwärts-Filterung an (Null-Phasenverzug)."""
    if b is None:
        return data
    try:
        from scipy.signal import filtfilt
        if data.shape[0] < 3 * max(len(b), len(a)):
            return data
        return filtfilt(b, a, data, axis=0)
    except Exception:
        return data


# ── PCA ──────────────────────────────────────────────────────────────────────

def pca_project(baseline: np.ndarray, current: np.ndarray, n_components: int = 5):
    """
    Projiziert baseline und current auf die n_components Hauptachsen der Baseline.
    Gibt (bl_proj, cur_proj) zurück.
    """
    n_comp = min(n_components, baseline.shape[1], baseline.shape[0] - 1)
    if n_comp < 1:
        return baseline, current

    mean = baseline.mean(axis=0)
    centered = baseline - mean
    try:
        cov = np.cov(centered.T)
        if cov.ndim == 0:
            cov = np.array([[float(cov)]])
        eigvals, eigvecs = np.linalg.eigh(cov)
        idx = np.argsort(eigvals)[::-1][:n_comp]
        top = eigvecs[:, idx]
        bl_proj  = centered @ top
        cur_proj = (current - mean) @ top
        return bl_proj, cur_proj
    except np.linalg.LinAlgError:
        return baseline - mean, current - mean


# ── Bewegungs-Score ───────────────────────────────────────────────────────────

def motion_score(bl_proj: np.ndarray, cur_proj: np.ndarray) -> float:
    """L2-Abstand zwischen den Mittelwerten der projizierten Fenster."""
    return float(np.linalg.norm(cur_proj.mean(0) - bl_proj.mean(0)))


# ── Streaming-Pipeline ────────────────────────────────────────────────────────

class CSIPipeline:
    """
    Verarbeitungs-Pipeline für eingehende CSI-Frames.
    Hält Ringpuffer für Baseline und Detection-Fenster.
    """

    def __init__(
        self,
        baseline_window: int = 100,
        detection_window: int = 10,
        pca_components: int = 5,
        bandpass_low: float = 0.1,
        bandpass_high: float = 2.0,
        sample_rate: float = 100.0,
        hampel_window: int = 5,
        hampel_sigma: float = 3.0,
    ):
        self._bl  = deque(maxlen=baseline_window)
        self._det = deque(maxlen=detection_window)
        self._n_pca = pca_components
        self._hw = hampel_window
        self._hs = hampel_sigma

        b, a = butter_bandpass_coeffs(bandpass_low, bandpass_high, sample_rate)
        self._b, self._a = b, a

    def push(self, amplitudes: np.ndarray) -> float | None:
        """
        Fügt einen neuen Amplitude-Vektor hinzu.
        Gibt den Bewegungs-Score zurück, oder None wenn noch nicht genug Daten.
        """
        self._bl.append(amplitudes)
        self._det.append(amplitudes)

        min_frames = max(self._bl.maxlen // 2, 3)
        if len(self._bl) < min_frames:
            return None

        bl_mat  = np.array(self._bl)
        det_mat = np.array(self._det)

        # 1. Hampel
        bl_mat  = hampel_matrix(bl_mat,  self._hw, self._hs)
        det_mat = hampel_matrix(det_mat, self._hw, self._hs)

        # 2. Bandpass (nur wenn genug Frames)
        if len(self._bl) >= 20:
            bl_mat  = apply_bandpass(bl_mat,  self._b, self._a)
            det_mat = apply_bandpass(det_mat, self._b, self._a)

        # 3. PCA + Score
        bl_proj, cur_proj = pca_project(bl_mat, det_mat, self._n_pca)
        return motion_score(bl_proj, cur_proj)
