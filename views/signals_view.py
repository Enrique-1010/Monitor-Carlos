# views/signals_view.py

import os
import io
import base64
import csv
import re
import uuid

import numpy as np
import plotly.graph_objects as go

from ui_charts import apply_chart_style, graph_config

import dash
from dash import html, dcc, Input, Output, State
from dash.exceptions import PreventUpdate
from flask import session


# ========= Helpers comunes =========

def smooth(x: np.ndarray, win_ms: int, fs: int):
    x = np.asarray(x, dtype=float)
    if x.size == 0:
        return x

    win = max(3, int(round(win_ms * fs / 1000)))
    if win % 2 == 0:
        win += 1
    if win >= len(x):
        win = max(3, (len(x) // 2) * 2 + 1)
    k = np.ones(win) / win
    return np.convolve(x, k, mode="same")


def _find_peaks_simple(s, height, distance):
    s = np.asarray(s)
    n = len(s)
    if n < 3:
        return np.array([], dtype=int)
    cand = np.where((s[1:-1] > s[:-2]) & (s[1:-1] >= s[2:]) & (s[1:-1] >= height))[0] + 1
    if cand.size == 0:
        return cand
    order = cand[np.argsort(s[cand])[::-1]]
    kept = []
    blocked = np.zeros(n, dtype=bool)
    for idx in order:
        a = max(0, idx - distance)
        b = min(n, idx + distance + 1)
        if blocked[a:b].any():
            continue
        kept.append(idx)
        blocked[a:b] = True
    return np.array(sorted(kept), dtype=int)


def kpi_card(label, value, suffix=""):
    return html.Div(className="kpi", children=[
        html.Div(label, className="kpi-label"),
        html.Div(f"{value}{suffix}", className="kpi-value"),
        html.Div(className="kpi-ecg-line")
    ])


# ========= Upload safety helpers =========

_ALLOWED_EXTS = {".csv"}


def _sanitize_filename(filename: str, default: str = "file.csv") -> str:
    """
    - Quita rutas (basename)
    - Permite solo [a-zA-Z0-9._-]
    - Normaliza espacios a _
    - Fuerza extensión permitida (csv)
    - Limita longitud
    """
    name = (filename or "").strip()
    name = os.path.basename(name)
    if not name:
        name = default

    name = name.replace(" ", "_")
    name = re.sub(r"[^a-zA-Z0-9._-]", "", name)

    if name in (".", "..") or not name:
        name = default

    base, ext = os.path.splitext(name)
    ext = (ext or "").lower()

    if ext not in _ALLOWED_EXTS:
        ext = ".csv"
        base = base if base else os.path.splitext(default)[0]

    base = (base or "file")[:80]
    return f"{base}{ext}"


def _save_unique(dirpath: str, filename: str, data: bytes) -> str:
    """
    Guarda evitando sobrescritura. Si ya existe, agrega sufijo _<id>.
    Devuelve el nombre final.
    """
    os.makedirs(dirpath, exist_ok=True)

    safe = _sanitize_filename(filename, default="file.csv")
    base, ext = os.path.splitext(safe)
    candidate = safe
    full = os.path.join(dirpath, candidate)

    if os.path.exists(full):
        suffix = uuid.uuid4().hex[:8]
        candidate = f"{base}_{suffix}{ext}"
        full = os.path.join(dirpath, candidate)

    with open(full, "wb") as f:
        f.write(data)

    return candidate


def _b64_to_bytes(content: str):
    if not content:
        raise ValueError("Contenido vacío")
    try:
        _, b64 = content.split(",", 1)
        return base64.b64decode(b64)
    except Exception as e:
        raise ValueError("Base64 inválido") from e


# ========= ECG =========

def read_ecg_csv(path: str, fs_default: int = 250):
    with open(path, newline='', encoding='utf-8', errors="ignore") as f:
        reader = csv.reader(f)
        rows = list(reader)
    if not rows:
        return np.array([]), np.array([]), fs_default

    header = [h.strip().lower() for h in rows[0]]
    has_header = any(header) and ("ecg" in header or "time" in header or "tiempo" in header)
    data_rows = rows[1:] if has_header else rows

    time_col = None
    ecg_col = None
    if has_header:
        for i, name in enumerate(header):
            if name in ("time", "tiempo"):
                time_col = i
            if name == "ecg":
                ecg_col = i
    if ecg_col is None:
        ecg_col = 0

    x_vals, t_vals = [], []
    for r in data_rows:
        if not r or all((c or "").strip() == "" for c in r):
            continue
        try:
            x_vals.append(float((r[ecg_col] or "").replace(",", ".")))
        except Exception:
            continue
        if time_col is not None and time_col < len(r):
            try:
                t_vals.append(float((r[time_col] or "").replace(",", ".")))
            except Exception:
                t_vals.append(None)
        else:
            t_vals.append(None)

    x = np.array(x_vals, dtype=float)
    has_time = all(v is not None for v in t_vals) and len(t_vals) > 1
    if has_time:
        t = np.array(t_vals, dtype=float)
        diffs = np.diff(t)
        fs = int(round(1.0 / np.mean(diffs))) if np.all(diffs > 0) else fs_default
    else:
        fs = fs_default
        t = np.arange(len(x)) / fs
    return t, x, fs


# ✅ Cache ligero para evitar lecturas repetidas del mismo archivo con sliders
_ECG_CACHE = {}
_ECG_CACHE_MAX = 16


def _cached_read_ecg_csv(path: str, fs_default: int = 250):
    try:
        mtime = os.path.getmtime(path)
    except Exception:
        mtime = None
    key = (path, mtime, int(fs_default or 250))
    if key in _ECG_CACHE:
        return _ECG_CACHE[key]
    out = read_ecg_csv(path, fs_default=fs_default)
    if len(_ECG_CACHE) >= _ECG_CACHE_MAX:
        try:
            _ECG_CACHE.pop(next(iter(_ECG_CACHE)))
        except Exception:
            _ECG_CACHE.clear()
    _ECG_CACHE[key] = out
    return out


def detect_r_peaks(x: np.ndarray, fs: int, sens: float = 0.6):
    x = np.asarray(x, dtype=float)
    if x.size == 0:
        return np.array([], dtype=int)

    z = (x - np.median(x))
    env = smooth(np.abs(z), win_ms=80, fs=fs)
    thr = np.quantile(env, sens)
    dist = int(0.25 * fs)
    peaks = _find_peaks_simple(env, height=thr, distance=dist)
    return peaks


def ecg_metrics_from_peaks(peaks: np.ndarray, fs: int):
    if peaks is None or len(peaks) < 2:
        return 0.0, 0.0, 0.0
    rr = np.diff(peaks) / fs
    bpm = 60.0 / np.mean(rr)
    sdnn = 1000 * np.std(rr)
    rmssd = 1000 * np.sqrt(np.mean(np.diff(rr) ** 2))
    return float(bpm), float(sdnn), float(rmssd)


def fig_ecg(t_line, x_line, peaks_t=None, peaks_y=None, title="ECG"):
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=t_line, y=x_line, mode="lines", name="ECG",
        line=dict(color="#00f28a", width=2)
    ))
    if peaks_t is not None and peaks_y is not None and len(peaks_t) > 0:
        fig.add_trace(go.Scatter(
            x=peaks_t, y=peaks_y,
            mode="markers", name="Picos R",
            marker=dict(size=7, symbol="x", color="#00f28a")
        ))

    apply_chart_style(
        fig,
        title=title,
        x_title="Tiempo (s)",
        y_title="Amplitud (a.u.)",
        height=420,
    )
    return fig

def kpi_grid_ecg(bpm, sdnn, rmssd):
    return [
        kpi_card("BPM", f"{bpm:.0f}"),
        kpi_card("SDNN", f"{sdnn:.0f}", " ms"),
        kpi_card("RMSSD", f"{rmssd:.0f}", " ms"),
    ]


# ========= IMU =========

def read_imu_csv(path: str, fs_default: int = 100):
    with open(path, newline="", encoding="utf-8", errors="ignore") as f:
        reader = csv.reader(f)
        rows = list(reader)

    if not rows:
        return np.array([]), np.array([]), fs_default

    header = [h.strip().lower() for h in rows[0]]
    has_header = any(header)

    if len(header) == 1 and (";" in header[0] or "\t" in header[0]):
        with open(path, encoding="utf-8", errors="ignore") as f:
            raw_lines = f.read().splitlines()
        sep = ";" if ";" in header[0] else "\t"
        rows = [line.split(sep) for line in raw_lines]
        header = [h.strip().lower() for h in rows[0]]
        has_header = any(header)

    if has_header and any(c in header for c in ("ax", "ay", "az")):
        data_rows = rows[1:]
        try:
            time_idx = header.index("time") if "time" in header else None
        except ValueError:
            time_idx = None
        try:
            ax_idx = header.index("ax")
            ay_idx = header.index("ay")
            az_idx = header.index("az")
        except ValueError:
            ax_idx, ay_idx, az_idx = 0, 1, 2
    else:
        data_rows = rows
        time_idx, ax_idx, ay_idx, az_idx = None, 0, 1, 2

    def to_float(s: str):
        s = (s or "").strip()
        if s == "":
            raise ValueError
        s = s.replace(",", ".")
        return float(s)

    t_vals, mag_vals = [], []
    for r in data_rows:
        if not r or all((c or "").strip() == "" for c in r):
            continue
        try:
            ax = to_float(r[ax_idx]) if ax_idx < len(r) else None
            ay = to_float(r[ay_idx]) if ay_idx < len(r) else None
            az = to_float(r[az_idx]) if az_idx < len(r) else None
            if None in (ax, ay, az):
                continue
        except Exception:
            continue

        mag = (ax ** 2 + ay ** 2 + az ** 2) ** 0.5
        mag_vals.append(mag)

        if time_idx is not None and time_idx < len(r):
            try:
                t_vals.append(to_float(r[time_idx]))
            except Exception:
                t_vals.append(None)
        else:
            t_vals.append(None)

    mag = np.array(mag_vals, dtype=float)

    has_time = all(v is not None for v in t_vals) and len(t_vals) > 1
    if has_time:
        t = np.array(t_vals, dtype=float)
        diffs = np.diff(t)
        fs = int(round(1.0 / np.mean(diffs))) if np.all(diffs > 0) else fs_default
    else:
        fs = fs_default
        t = np.arange(len(mag)) / fs

    return t, mag, fs


def imu_metrics_from_mag(mag: np.ndarray, t: np.ndarray, fs: int):
    if mag is None or len(mag) < 5:
        return 0, 0.0, 0.0, 0.0, np.array([], dtype=int)

    thr = float(np.quantile(mag, 0.90))
    dist = max(1, int(0.1 * fs))
    peaks = _find_peaks_simple(mag, height=thr, distance=dist)

    duration = float(t[-1] - t[0]) if len(t) > 1 else 0.0
    n_hits = int(len(peaks))
    hits_per_min = (n_hits / (duration / 60.0)) if duration > 0 else 0.0

    if n_hits > 0:
        mean_int = float(np.mean(mag[peaks])) / 9.81  # g
        max_int = float(np.max(mag[peaks])) / 9.81
    else:
        mean_int = 0.0
        max_int = 0.0

    return n_hits, hits_per_min, mean_int, max_int, peaks


def fig_imu(t_line, mag_line, peaks_t=None, peaks_y=None, thr=None, title="Golpes / IMU"):
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=t_line, y=mag_line, mode="lines", name="|a| (m/s²)",
        line=dict(color="#00f28a", width=2)
    ))

    # Umbral visual (P90) — no cambia el algoritmo, solo ayuda a lectura
    if thr is not None and len(t_line) > 1:
        try:
            fig.add_shape(
                type="line",
                x0=float(t_line[0]), x1=float(t_line[-1]),
                y0=float(thr), y1=float(thr),
                line=dict(color="rgba(0,242,138,0.35)", width=2, dash="dash"),
            )
            fig.add_annotation(
                x=float(t_line[0]),
                y=float(thr),
                text=f"Umbral (P90) ≈ {float(thr)/9.81:.2f} g",
                showarrow=False,
                xanchor="left",
                yanchor="bottom",
                font=dict(size=12, color="rgba(231,236,243,0.85)"),
                bgcolor="rgba(15,22,35,0.35)",
                bordercolor="rgba(44,61,85,0.55)",
                borderwidth=1,
                borderpad=4,
            )
        except Exception:
            pass

    if peaks_t is not None and peaks_y is not None and len(peaks_t) > 0:
        fig.add_trace(go.Scatter(
            x=peaks_t, y=peaks_y,
            mode="markers", name="Eventos detectados",
            marker=dict(symbol="x", size=8, color="#00f28a")
        ))

    apply_chart_style(
        fig,
        title=title,
        x_title="Tiempo (s)",
        y_title="|a| (m/s²)",
        height=420,
    )
    return fig

def kpi_grid_imu(n_hits, hits_per_min, mean_int, max_int):
    return [
        kpi_card("Eventos detectados", f"{n_hits}"),
        kpi_card("Eventos/minuto", f"{hits_per_min:.1f}"),
        kpi_card("Intensidad media", f"{mean_int:.2f}", " g"),
        kpi_card("Intensidad pico", f"{max_int:.2f}", " g"),
    ]
# ========= EMG =========

def read_emg_csv(path: str, fs_default: int = 1000):
    with open(path, newline="", encoding="utf-8", errors="ignore") as f:
        reader = csv.reader(f)
        rows = list(reader)

    if not rows:
        return np.array([]), np.array([]), fs_default

    header = [h.strip().lower() for h in rows[0]]
    has_header = any(header)
    data_rows = rows[1:] if has_header else rows

    time_idx = None
    emg_idx = None
    if has_header:
        for i, name in enumerate(header):
            if name in ("time", "tiempo"):
                time_idx = i
            if name in ("emg", "ch1", "signal") and emg_idx is None:
                emg_idx = i
        if emg_idx is None:
            for i, name in enumerate(header):
                if i != time_idx:
                    emg_idx = i
                    break
    else:
        time_idx, emg_idx = None, 0
        data_rows = rows

    def to_float(s: str):
        s = (s or "").strip()
        if s == "":
            raise ValueError
        s = s.replace(",", ".")
        return float(s)

    t_vals, x_vals = [], []
    for r in data_rows:
        if not r or all((c or "").strip() == "" for c in r):
            continue
        try:
            x_vals.append(to_float(r[emg_idx]))
        except Exception:
            continue

        if time_idx is not None and time_idx < len(r):
            try:
                t_vals.append(to_float(r[time_idx]))
            except Exception:
                t_vals.append(None)
        else:
            t_vals.append(None)

    x = np.array(x_vals, dtype=float)
    has_time = all(v is not None for v in t_vals) and len(t_vals) > 1
    if has_time:
        t = np.array(t_vals, dtype=float)
        diffs = np.diff(t)
        fs = int(round(1.0 / np.mean(diffs))) if np.all(diffs > 0) else fs_default
    else:
        fs = fs_default
        t = np.arange(len(x)) / fs

    return t, x, fs


def emg_metrics(x: np.ndarray, fs: int):
    x = np.asarray(x, dtype=float)
    if x.size == 0:
        return 0.0, 0.0, 0.0

    x0 = x - np.mean(x)
    rms_global = float(np.sqrt(np.mean(x0 ** 2)))
    peak = float(np.max(np.abs(x0)))

    n = len(x0)
    if n < 30:
        fatigue = 0.0
    else:
        third = n // 3
        first_rms = float(np.sqrt(np.mean(x0[:third] ** 2)))
        last_rms = float(np.sqrt(np.mean(x0[-third:] ** 2)))
        if first_rms > 1e-6:
            fatigue = max(0.0, min(100.0, 100.0 * (1.0 - last_rms / first_rms)))
        else:
            fatigue = 0.0

    return rms_global, peak, fatigue


def fig_emg(t_line, env_line, fs: int, thr=None, title="EMG"):
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=t_line, y=env_line, mode="lines", name="EMG (envolvente)",
        line=dict(color="#00f28a", width=2)
    ))

    # Umbral visual (P90) — no cambia el algoritmo, solo ayuda a lectura
    if thr is not None and len(t_line) > 1:
        try:
            fig.add_shape(
                type="line",
                x0=float(t_line[0]), x1=float(t_line[-1]),
                y0=float(thr), y1=float(thr),
                line=dict(color="rgba(0,242,138,0.35)", width=2, dash="dash"),
            )
            fig.add_annotation(
                x=float(t_line[0]),
                y=float(thr),
                text=f"Umbral (P90) ≈ {float(thr):.3f}",
                showarrow=False,
                xanchor="left",
                yanchor="bottom",
                font=dict(size=12, color="rgba(231,236,243,0.85)"),
                bgcolor="rgba(15,22,35,0.35)",
                bordercolor="rgba(44,61,85,0.55)",
                borderwidth=1,
                borderpad=4,
            )
        except Exception:
            pass

    apply_chart_style(
        fig,
        title=title,
        x_title="Tiempo (s)",
        y_title="Amplitud (a.u.)",
        height=420,
    )
    return fig

def kpi_grid_emg(rms, peak, fatigue):
    return [
        kpi_card("RMS global", f"{rms:.3f}"),
        kpi_card("Pico absoluto", f"{peak:.3f}"),
        kpi_card("Fatiga estimada", f"{fatigue:.1f}", " %"),
    ]


# ========= Respiración =========

def read_resp_csv(path: str, fs_default: int = 25):
    with open(path, newline="", encoding="utf-8", errors="ignore") as f:
        reader = csv.reader(f)
        rows = list(reader)

    if not rows:
        return np.array([]), np.array([]), fs_default

    header = [h.strip().lower() for h in rows[0]]
    has_header = any(header)
    data_rows = rows[1:] if has_header else rows

    time_idx, resp_idx = None, None
    if has_header:
        for i, name in enumerate(header):
            if name in ("time", "tiempo"):
                time_idx = i
            if name in ("resp", "breath", "band") and resp_idx is None:
                resp_idx = i
        if resp_idx is None:
            for i, name in enumerate(header):
                if i != time_idx:
                    resp_idx = i
                    break
    else:
        time_idx, resp_idx = None, 0

    def to_float(s: str):
        s = (s or "").strip()
        if s == "":
            raise ValueError
        s = s.replace(",", ".")
        return float(s)

    t_vals, x_vals = [], []
    for r in data_rows:
        if not r or all((c or "").strip() == "" for c in r):
            continue
        try:
            x_vals.append(to_float(r[resp_idx]))
        except Exception:
            continue

        if time_idx is not None and time_idx < len(r):
            try:
                t_vals.append(to_float(r[time_idx]))
            except Exception:
                t_vals.append(None)
        else:
            t_vals.append(None)

    x = np.array(x_vals, dtype=float)
    has_time = all(v is not None for v in t_vals) and len(t_vals) > 1
    if has_time:
        t = np.array(t_vals, dtype=float)
        diffs = np.diff(t)
        fs = int(round(1.0 / np.mean(diffs))) if np.all(diffs > 0) else fs_default
    else:
        fs = fs_default
        t = np.arange(len(x)) / fs

    return t, x, fs


def resp_metrics(t: np.ndarray, x: np.ndarray, fs: int, sens: float = 0.6):
    x = np.asarray(x, dtype=float)
    if x.size == 0:
        return 0, 0.0, 0.0, np.array([], dtype=int)

    x0 = x - np.mean(x)
    env = smooth(x0, win_ms=250, fs=fs)
    thr = np.quantile(env, sens)
    dist = int(0.8 * fs)
    peaks = _find_peaks_simple(env, height=thr, distance=dist)

    n_breaths = int(len(peaks))
    duration = float(t[-1] - t[0]) if len(t) > 1 else 0.0
    br_min = (n_breaths / (duration / 60.0)) if duration > 0 else 0.0

    if n_breaths > 1:
        periods = np.diff(t[peaks])
        mean_period = float(np.mean(periods))
    else:
        mean_period = 0.0

    return n_breaths, br_min, mean_period, peaks


def fig_resp(t_line, env_line, peaks_t=None, peaks_y=None, thr=None, title="Respiración"):
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=t_line, y=env_line, mode="lines", name="Resp (filtrada)",
        line=dict(color="#00f28a", width=2)
    ))

    # Umbral visual (P90) — no cambia el algoritmo, solo ayuda a lectura
    if thr is not None and len(t_line) > 1:
        try:
            fig.add_shape(
                type="line",
                x0=float(t_line[0]), x1=float(t_line[-1]),
                y0=float(thr), y1=float(thr),
                line=dict(color="rgba(0,242,138,0.35)", width=2, dash="dash"),
            )
            fig.add_annotation(
                x=float(t_line[0]),
                y=float(thr),
                text=f"Umbral (P90) ≈ {float(thr):.3f}",
                showarrow=False,
                xanchor="left",
                yanchor="bottom",
                font=dict(size=12, color="rgba(231,236,243,0.85)"),
                bgcolor="rgba(15,22,35,0.35)",
                bordercolor="rgba(44,61,85,0.55)",
                borderwidth=1,
                borderpad=4,
            )
        except Exception:
            pass

    if peaks_t is not None and peaks_y is not None and len(peaks_t) > 0:
        fig.add_trace(go.Scatter(
            x=peaks_t, y=peaks_y,
            mode="markers", name="Inhalaciones",
            marker=dict(symbol="x", size=8, color="#00f28a")
        ))

    apply_chart_style(
        fig,
        title=title,
        x_title="Tiempo (s)",
        y_title="Amplitud (a.u.)",
        height=420,
    )
    return fig

def kpi_grid_resp(n_breaths, br_min, mean_period):
    return [
        kpi_card("Respiraciones", f"{n_breaths}"),
        kpi_card("Resp/min", f"{br_min:.1f}"),
        kpi_card("Periodo medio", f"{mean_period:.2f}", " s"),
    ]


# ========= Clase principal =========

class SignalsView:
    """
    Vista de 'Cargar señales', organizada por sensor:
    ECG/HRV, IMU (brazo/pierna/cabeza), EMG (brazo/pierna) y banda de respiración.
    """

    # Soporta distintos nombres por si en DB guardaste códigos distintos
    _SENSOR_ALIASES = {
        "ECG": {"ECG"},
        "IMU": {"IMU", "IMU_ARM", "IMU_LEG", "IMU_HEAD"},
        "EMG": {"EMG", "EMG_ARM", "EMG_LEG"},
        "RESP_BELT": {"RESP_BELT", "RESP", "RESPIRATION", "BREATH"},
    }

    def __init__(self, app: dash.Dash, db, sensors_module):
        self.app = app
        self.db = db
        self.S = sensors_module
        self._register_callbacks()

    def _safe_int(self, x):
        try:
            return int(x)
        except Exception:
            return None

    def _sparse_marks(self, max_s: float) -> dict:
        """Genera marcas legibles para el RangeSlider (evita solape de números)."""
        try:
            ms = float(max_s)
        except Exception:
            ms = 10.0
        if ms <= 0:
            ms = 10.0

        if ms <= 20:
            step = 1
        elif ms <= 90:
            step = 5
        elif ms <= 300:
            step = 15
        elif ms <= 900:
            step = 30
        else:
            step = 60

        end = int(round(ms))
        marks = {0: "0"}
        for v in range(step, end, step):
            marks[v] = str(v)
        marks[end] = str(end)
        return marks

    def _has_sensor(self, user_id: int, sensor_key: str) -> bool:
        try:
            codes = set(self.db.get_user_sensors(int(user_id)) or [])
        except Exception:
            codes = set()
        aliases = self._SENSOR_ALIASES.get(sensor_key, {sensor_key})
        return len(codes.intersection(aliases)) > 0

    # ---------- Layout ----------

    def layout(self):
        role = (session.get("role") or "no autenticado")
        uid = session.get("user_id")

        if role == "coach" and uid:
            athletes = self.db.list_athletes_for_coach(int(uid))
        elif role == "deportista" and uid:
            u = self.db.get_user_by_id(int(uid))
            athletes = [u] if u and u.get("role") == "deportista" else []
        else:
            athletes = [u for u in self.db.list_users()
                        if (u.get("role", "deportista") == "deportista")]

        options_users = [
            {"label": f"{u['name']} · {u.get('sport', '-')}", "value": u["id"]}
            for u in athletes
        ]
        default_user = options_users[0]["value"] if options_users else None

        sensors_text = "Inicia sesión como deportista para ver tus sensores."
        if uid and role == "deportista":
            try:
                codes = self.db.get_user_sensors(int(uid)) or []
            except Exception:
                codes = []
            if codes:
                labels = [self.S.catalog()[c]["short"] for c in codes if c in self.S.catalog()]
                sensors_text = " · ".join(labels) if labels else "Sensores asignados (sin etiquetas)."
            else:
                sensors_text = "Sin sensores asignados aún."

        if role == "deportista":
            user_selector = html.Div([
                html.Label("Deportista"),
                dcc.Dropdown(
                    id="ecg-user",
                    options=options_users,
                    value=default_user,
                    disabled=True,
                )
            ])
        else:
            user_selector = html.Div([
                html.Label("Deportista"),
                dcc.Dropdown(
                    id="ecg-user",
                    options=options_users,
                    placeholder="Selecciona deportista..."
                )
            ])

        # ✅ Sesión activa (nuevo)
        session_block = html.Div(
            style={"marginTop": "10px", "marginBottom": "10px"},
            children=[
                html.Label("Sesión activa (opcional)"),
                dcc.Dropdown(
                    id="signals-session",
                    options=[],
                    placeholder="Selecciona una sesión... (o crea una nueva)",
                    clearable=True,
                ),
                html.Div(style={"marginTop": "8px"}, children=[
                    html.Button("Nueva sesión", id="btn-new-session", className="btn btn-primary"),
                    html.Button("Cerrar sesión", id="btn-close-session", className="btn btn-ghost", style={"marginLeft": "10px"}),
                ]),
                html.Div(id="session-msg", style={"marginTop": "8px", "color": "#FFB4B4"}),
            ],
        )

        # ----- ECG -----
        ecg_block = html.Div(style={
            "display": "grid",
            "gridTemplateColumns": "1fr 1fr",
            "gap": "16px"
        }, children=[
            html.Div(children=[
                html.Small("Sólo deportistas pueden tener ficheros ECG.", style={"opacity": 0.8}),
                html.Br(),
                html.Label("Subir archivo ECG (.csv)"),
                dcc.Upload(
                    id="ecg-upload",
                    children=html.Div("Arrastra o elige un archivo"),
                    multiple=False,
                    style={
                        "padding": "12px",
                        "border": "1px dashed #2b3a52",
                        "borderRadius": "10px"
                    }
                ),
                html.Button("Cargar ECG de ejemplo", id="btn-ecg-demo",
                            style={"marginTop": "10px"}, className="btn btn-ghost"),
                html.Br(), html.Br(),
                html.Label("Ficheros ECG del usuario"),
                dcc.Dropdown(id="ecg-file", placeholder="No hay archivos aún..."),
                html.Br(),
                html.Div(className="filters-bar filters-bar--2", children=[
                    html.Div(className="filter-item", children=[
                        html.Label("Ventana (s)"),
                        dcc.Dropdown(
                            id="ecg-winlen",
                            options=[
                                {"label": "5s", "value": 5},
                                {"label": "10s", "value": 10},
                                {"label": "20s", "value": 20},
                                {"label": "30s", "value": 30},
                                {"label": "60s", "value": 60},
                                {"label": "120s", "value": 120},
                                {"label": "Todo", "value": -1},
                            ],
                            value=10,
                            clearable=False,
                        ),
                    ]),
                    html.Div(className="filter-item", children=[
                        html.Label("Calidad render"),
                        dcc.Dropdown(
                            id="ecg-quality",
                            options=[
                                {"label": "Alta", "value": "high"},
                                {"label": "Media", "value": "med"},
                                {"label": "Ligera", "value": "low"},
                            ],
                            value="med",
                            clearable=False,
                        ),
                    ]),
                ]),
                html.Div(className="filter-item", children=[
                    html.Label("Rango visible (s)"),
                    dcc.RangeSlider(
                        id="ecg-window",
                        min=0,
                        max=10,
                        step=0.05,
                        value=[0, 10],
                        marks={0: "0", 10: "10"},
                        tooltip={"placement": "bottom"},
                        updatemode="mouseup",
                        allowCross=False,
                    ),
                    html.Small(
                        "Tip: esta ventana afecta solo la VISUALIZACIÓN (métricas = señal completa).",
                        className="text-muted"
                    ),
                ]),
                dcc.Checklist(
                    options=[{"label": " Mostrar picos R", "value": "r"}],
                    value=[], id="ecg-showr"
                ),
                html.Label("Sensibilidad picos (umbral)"),
                dcc.Slider(
                    id="ecg-sens",
                    min=0.3, max=0.95, step=0.05,
                    value=0.6, tooltip={"placement": "bottom"},
                    updatemode="mouseup"
                ),
                html.Label("Suavizado (ms)"),
                dcc.Slider(
                    id="ecg-smooth",
                    min=20, max=120, step=5,
                    value=40, tooltip={"placement": "bottom"},
                    updatemode="mouseup"
                ),
                html.Div(className="export-actions", children=[
                html.Button("Descargar PNG", id="btn-dl-png", className="btn btn-primary"),
                html.Button("Descargar picos (CSV)", id="btn-dl-peaks", className="btn btn-ghost"),
            ]),
            html.Small(
                "Tip: si el PNG falla, instala kaleido (python -m pip install -U kaleido).",
                className="text-muted"
            ),
                html.Div(id="ecg-msg", style={"marginTop": "8px", "color": "#FFB4B4"})
            ]),
            html.Div(children=[
                html.Div(id="ecg-kpis", className="kpis"),
                html.Div(className="ecg-divider"),
                dcc.Graph(id="ecg-graph", figure=go.Figure(), config=graph_config(), style={"height": "420px", "width": "100%"})
            ])
        ])

        # ----- IMU -----
        imu_block = html.Div(
            style={
                "display": "grid",
                "gridTemplateColumns": "1fr 1fr",
                "gap": "16px",
                "marginTop": "16px",
            },
            children=[
                html.Div(children=[
                    html.Label("Tipo de análisis IMU"),
                    dcc.Tabs(
                        id="imu-tabs",
                        value="imu-arm",
                        children=[
                            dcc.Tab(label="Golpes brazo (IMU guante)", value="imu-arm"),
                            dcc.Tab(label="Patadas (IMU pierna)", value="imu-leg"),
                            dcc.Tab(label="Impactos cabeza (IMU casco)", value="imu-head"),
                        ],
                        style={"marginBottom": "8px"},
                    ),

                    html.Label("Archivo IMU (.csv)"),
                    dcc.Upload(
                        id="imu-upload",
                        children=html.Div("Arrastra o elige un archivo de IMU"),
                        multiple=False,
                        style={
                            "padding": "12px",
                            "border": "1px dashed #2b3a52",
                            "borderRadius": "10px",
                        },
                    ),
                    html.Br(),
                    html.Button("Analizar golpes / impactos", id="btn-imu-analyze", className="btn btn-primary"),
                    html.Div(id="imu-msg", style={"marginTop": "8px", "color": "#FFB4B4"}),
                    html.Div(className="filters-bar filters-bar--2", children=[
                        html.Div(className="filter-item", children=[
                            html.Label("Ventana (s)"),
                            dcc.Dropdown(
                                id="imu-winlen",
                                options=[
                                    {"label": "5s", "value": 5},
                                    {"label": "10s", "value": 10},
                                    {"label": "20s", "value": 20},
                                    {"label": "30s", "value": 30},
                                    {"label": "60s", "value": 60},
                                    {"label": "120s", "value": 120},
                                    {"label": "Todo", "value": -1},
                                ],
                                value=10,
                                clearable=False,
                            ),
                        ]),
                        html.Div(className="filter-item", children=[
                            html.Label("Calidad render"),
                            dcc.Dropdown(
                                id="imu-quality",
                                options=[
                                    {"label": "Alta", "value": "high"},
                                    {"label": "Media", "value": "med"},
                                    {"label": "Ligera", "value": "low"},
                                ],
                                value="med",
                                clearable=False,
                            ),
                        ]),
                    ]),
                    html.Div(className="filter-item", children=[
                        html.Label("Rango visible (s)"),
                        dcc.RangeSlider(
                            id="imu-window",
                            min=0,
                            max=10,
                            step=0.05,
                            value=[0, 10],
                            marks={0: "0", 10: "10"},
                            tooltip={"placement": "bottom"},
                            updatemode="mouseup",
                            allowCross=False,
                        ),
                        html.Small(
                            "Tip: esta ventana afecta solo la VISUALIZACIÓN (métricas = señal completa).",
                            className="text-muted"
                        ),
                    ]),
                    html.Br(),
                    html.P(
                        [
                            "Formato recomendado: primera fila con cabeceras ",
                            html.Code("time,ax,ay,az"),
                            " y el tiempo en segundos. ",
                            "El algoritmo es el mismo, pero la interpretación cambia según la pestaña: ",
                            "brazo = frecuencia e intensidad de golpes; ",
                            "pierna = patadas; ",
                            "cabeza = impactos al casco.",
                        ],
                        className="muted",
                        style={"fontSize": "13px", "opacity": 0.7},
                    ),
                ]),
                html.Div(children=[
                    html.Div(id="imu-kpis", className="kpis"),
                    html.Div(className="ecg-divider"),
                    dcc.Graph(id="imu-graph", figure=go.Figure(), config=graph_config(), style={"height": "420px", "width": "100%"}),
                ]),
            ],
        )

        # ----- EMG -----
        emg_block = html.Div(
            style={
                "display": "grid",
                "gridTemplateColumns": "1fr 1fr",
                "gap": "16px",
                "marginTop": "16px",
            },
            children=[
                html.Div(children=[
                    html.Label("Canal EMG"),
                    dcc.Tabs(
                        id="emg-tabs",
                        value="emg-arm",
                        children=[
                            dcc.Tab(label="EMG brazo", value="emg-arm"),
                            dcc.Tab(label="EMG pierna", value="emg-leg"),
                        ],
                        style={"marginBottom": "8px"},
                    ),

                    html.Label("Archivo EMG (.csv)"),
                    dcc.Upload(
                        id="emg-upload",
                        children=html.Div("Arrastra o elige un archivo de EMG"),
                        multiple=False,
                        style={
                            "padding": "12px",
                            "border": "1px dashed #2b3a52",
                            "borderRadius": "10px",
                        },
                    ),
                    html.Br(),
                    html.Label("Ventana RMS (ms)"),
                    dcc.Slider(
                        id="emg-win",
                        min=20,
                        max=250,
                        step=10,
                        value=100,
                        tooltip={"placement": "bottom"},
                        updatemode="mouseup"
                    ),
                    html.Br(),
                    html.Button("Analizar EMG", id="btn-emg-analyze", className="btn btn-primary"),
                    html.Div(id="emg-msg", style={"marginTop": "8px", "color": "#FFB4B4"}),
                    html.Div(className="filters-bar filters-bar--2", children=[
                        html.Div(className="filter-item", children=[
                            html.Label("Ventana (s)"),
                            dcc.Dropdown(
                                id="emg-winlen",
                                options=[
                                    {"label": "5s", "value": 5},
                                    {"label": "10s", "value": 10},
                                    {"label": "20s", "value": 20},
                                    {"label": "30s", "value": 30},
                                    {"label": "60s", "value": 60},
                                    {"label": "120s", "value": 120},
                                    {"label": "Todo", "value": -1},
                                ],
                                value=10,
                                clearable=False,
                            ),
                        ]),
                        html.Div(className="filter-item", children=[
                            html.Label("Calidad render"),
                            dcc.Dropdown(
                                id="emg-quality",
                                options=[
                                    {"label": "Alta", "value": "high"},
                                    {"label": "Media", "value": "med"},
                                    {"label": "Ligera", "value": "low"},
                                ],
                                value="med",
                                clearable=False,
                            ),
                        ]),
                    ]),
                    html.Div(className="filter-item", children=[
                        html.Label("Rango visible (s)"),
                        dcc.RangeSlider(
                            id="emg-window",
                            min=0,
                            max=10,
                            step=0.05,
                            value=[0, 10],
                            marks={0: "0", 10: "10"},
                            tooltip={"placement": "bottom"},
                            updatemode="mouseup",
                            allowCross=False,
                        ),
                        html.Small(
                            "Tip: esta ventana afecta solo la VISUALIZACIÓN (métricas = señal completa).",
                            className="text-muted"
                        ),
                    ]),
                    html.Br(),
                    html.P(
                        [
                            "Formato recomendado: ",
                            html.Code("time,emg"),
                            " o ",
                            html.Code("time,ch1"),
                            ". La lógica es la misma para brazo y pierna, pero ",
                            "la interpretación cambia (brazo: golpes / guardia, pierna: patadas / desplazamientos).",
                        ],
                        className="muted",
                        style={"fontSize": "13px", "opacity": 0.7},
                    ),
                ]),
                html.Div(children=[
                    html.Div(id="emg-kpis", className="kpis"),
                    html.Div(className="ecg-divider"),
                    dcc.Graph(id="emg-graph", figure=go.Figure(), config=graph_config(), style={"height": "420px", "width": "100%"}),
                ]),
            ],
        )

        # ----- RESP -----
        resp_block = html.Div(style={
            "display": "grid",
            "gridTemplateColumns": "1fr 1fr",
            "gap": "16px",
            "marginTop": "16px"
        }, children=[
            html.Div(children=[
                html.Label("Archivo respiración (.csv)"),
                dcc.Upload(
                    id="resp-upload",
                    children=html.Div("Arrastra o elige un archivo de banda respiratoria"),
                    multiple=False,
                    style={
                        "padding": "12px",
                        "border": "1px dashed #2b3a52",
                        "borderRadius": "10px"
                    }
                ),
                html.Br(),
                html.Div(className="filters-bar filters-bar--3", children=[
                    html.Div(className="filter-item", children=[
                        html.Label("Sensibilidad detección"),
                        dcc.Slider(
                            id="resp-sens",
                            min=0.3, max=0.95, step=0.05,
                            value=0.6,
                            marks={0.3: "0.30", 0.5: "0.50", 0.6: "0.60", 0.7: "0.70", 0.95: "0.95"},
                            tooltip={"placement": "bottom", "always_visible": True},
                            updatemode="mouseup"
                        ),
                    ]),
                    html.Div(className="filter-item", children=[
                        html.Label("Ventana (s)"),
                        dcc.Dropdown(
                            id="resp-winlen",
                            options=[
                                {"label": "5s", "value": 5},
                                {"label": "10s", "value": 10},
                                {"label": "20s", "value": 20},
                                {"label": "30s", "value": 30},
                                {"label": "60s", "value": 60},
                                {"label": "120s", "value": 120},
                                {"label": "Todo", "value": -1},
                            ],
                            value=30,
                            clearable=False,
                        ),
                    ]),
                    html.Div(className="filter-item", children=[
                        html.Label("Calidad render"),
                        dcc.Dropdown(
                            id="resp-quality",
                            options=[
                                {"label": "Alta", "value": "high"},
                                {"label": "Media", "value": "med"},
                                {"label": "Ligera", "value": "low"},
                            ],
                            value="med",
                            clearable=False,
                        ),
                    ]),
                ]),
                html.Div(className="filter-item", children=[
                    html.Label("Rango visible (s)"),
                    dcc.RangeSlider(
                        id="resp-window",
                        min=0,
                        max=10,
                        step=0.05,
                        value=[0, 10],
                        marks={0: "0", 10: "10"},
                        tooltip={"placement": "bottom"},
                        updatemode="mouseup",
                        allowCross=False,
                    ),
                    html.Small(
                        "Tip: esta ventana afecta solo la VISUALIZACIÓN (métricas = señal completa).",
                        className="text-muted"
                    ),
                ]),

                html.Button("Analizar respiración", id="btn-resp-analyze", className="btn btn-primary"),
                html.Div(id="resp-msg", style={"marginTop": "8px", "color": "#FFB4B4"}),
                html.Br(),
                html.P(
                    "Formato recomendado: 'time,resp' con la banda torácica en unidades arbitrarias.",
                    className="muted",
                    style={"fontSize": "13px", "opacity": 0.7}
                ),
            ]),
            html.Div(children=[
                html.Div(id="resp-kpis", className="kpis"),
                html.Div(className="ecg-divider"),
                dcc.Graph(id="resp-graph", figure=go.Figure(), config=graph_config(), style={"height": "420px", "width": "100%"})
            ])
        ])

        # ✅ wrappers para bloquear interacción SIN romper callbacks
        def _wrap(lock_msg_id: str, lock_wrap_id: str, inner):
            return html.Div([
                html.Div(id=lock_msg_id, style={"marginBottom": "8px", "color": "#FFB4B4"}),
                html.Div(id=lock_wrap_id, children=[inner], style={}),
            ])

        return html.Div([
            # ✅ descargas (no visibles)
            dcc.Download(id="dl-png"),
            dcc.Download(id="dl-peaks"),
            dcc.Store(id="dl-png-clicks", data=0),
            dcc.Store(id="imu-meta", data=None),
            dcc.Store(id="emg-meta", data=None),
            dcc.Store(id="resp-meta", data=None),

            html.H2("Cargar señales"),
            html.Small(
                "ECG, golpes, EMG y respiración de tus sesiones de combate. "
                "La disponibilidad real depende de los sensores asignados.",
                style={"opacity": 0.8}
            ),
            html.Br(),
            html.Div(f"Sensores asignados: {sensors_text}",
                     className="muted",
                     style={"marginTop": "4px", "marginBottom": "8px"}),

            # ✅ banner dinámico según atleta seleccionado
            html.Div(id="signals-sensors-banner",
                     className="muted",
                     style={"marginBottom": "12px", "opacity": 0.85}),

            user_selector,
            session_block,
            html.Hr(),

            html.H3("ECG / HRV"),
            _wrap("ecg-lock-msg", "ecg-lock-wrapper", ecg_block),
            html.Hr(style={"marginTop": "24px"}),

            html.H3("Golpes / IMU"),
            _wrap("imu-lock-msg", "imu-lock-wrapper", imu_block),
            html.Hr(style={"marginTop": "24px"}),

            html.H3("EMG (brazo / pierna)"),
            _wrap("emg-lock-msg", "emg-lock-wrapper", emg_block),
            html.Hr(style={"marginTop": "24px"}),

            html.H3("Respiración (banda torácica)"),
            _wrap("resp-lock-msg", "resp-lock-wrapper", resp_block),
        ])

    # ---------- Callbacks ----------

    def _register_callbacks(self):
        app = self.app
        db = self.db

        def _safe_int(x):
            try:
                return int(x)
            except Exception:
                return None

        def _has_sensor(uid: int, sensor_key: str) -> bool:
            try:
                codes = set(db.get_user_sensors(int(uid)) or [])
            except Exception:
                codes = set()
            aliases = self._SENSOR_ALIASES.get(sensor_key, {sensor_key})
            return len(codes.intersection(aliases)) > 0

        def _lock_style(is_enabled: bool):
            if is_enabled:
                return {}
            return {
                "opacity": 0.35,
                "pointerEvents": "none",
                "filter": "grayscale(1)",
            }

        def _list_ecg_options(user_id: int):
            files = db.list_ecg_files(user_id) or []
            return [{"label": f["filename"], "value": f["id"]} for f in files if f.get("filename")]

        def _list_session_options(athlete_id: int):
            try:
                sessions = db.list_sessions(int(athlete_id), limit=50) or []
            except Exception:
                return []
            opts = []
            for s in sessions:
                sid = s.get("id")
                ts = (s.get("ts_start") or "")[:19].replace("T", " ")
                st = (s.get("status") or "—")
                label = f"#{sid} · {ts} · {st}"
                opts.append({"label": label, "value": sid})
            return opts

        # ✅ (PASO 3) GATING POR SENSORES (sin romper callbacks)
        @app.callback(
            Output("signals-sensors-banner", "children"),
            Output("ecg-lock-msg", "children"),
            Output("ecg-lock-wrapper", "style"),
            Output("imu-lock-msg", "children"),
            Output("imu-lock-wrapper", "style"),
            Output("emg-lock-msg", "children"),
            Output("emg-lock-wrapper", "style"),
            Output("resp-lock-msg", "children"),
            Output("resp-lock-wrapper", "style"),
            Input("ecg-user", "value"),
        )
        def gate_sections(user_id):
            if not user_id:
                return "", "", {}, "", {}, "", {}, "", {}

            uid = _safe_int(user_id)
            if not uid:
                return "", "", {}, "", {}, "", {}, "", {}

            ecg_ok = _has_sensor(uid, "ECG")
            imu_ok = _has_sensor(uid, "IMU")
            emg_ok = _has_sensor(uid, "EMG")
            resp_ok = _has_sensor(uid, "RESP_BELT")

            enabled = []
            missing = []
            for key, ok in [("ECG", ecg_ok), ("IMU", imu_ok), ("EMG", emg_ok), ("RESP_BELT", resp_ok)]:
                (enabled if ok else missing).append(key)

            banner = f"Habilitados: {', '.join(enabled) if enabled else '—'}"
            if missing:
                banner += f" · No asignados: {', '.join(missing)}"

            ecg_msg = "" if ecg_ok else "🔒 ECG no asignado para este deportista (asígnalo en 'Sensores & asignación')."
            imu_msg = "" if imu_ok else "🔒 IMU no asignado para este deportista."
            emg_msg = "" if emg_ok else "🔒 EMG no asignado para este deportista."
            resp_msg = "" if resp_ok else "🔒 Respiración no asignada para este deportista."

            return (
                banner,
                ecg_msg, _lock_style(ecg_ok),
                imu_msg, _lock_style(imu_ok),
                emg_msg, _lock_style(emg_ok),
                resp_msg, _lock_style(resp_ok),
            )

        # ✅ Sesiones: cargar / crear / cerrar con un solo callback (sin outputs duplicados)
        @app.callback(
            Output("signals-session", "options"),
            Output("signals-session", "value"),
            Output("session-msg", "children"),
            Input("ecg-user", "value"),
            Input("btn-new-session", "n_clicks"),
            Input("btn-close-session", "n_clicks"),
            State("signals-session", "value"),
        )
        def session_ui(user_id, n_new, n_close, current_session_id):
            if not user_id:
                return [], None, ""

            trig = ""
            try:
                if dash.callback_context.triggered:
                    trig = dash.callback_context.triggered[0]["prop_id"] or ""
            except Exception:
                trig = ""

            uid = _safe_int(user_id)
            if not uid:
                return [], None, ""

            # Cerrar sesión
            if trig.startswith("btn-close-session") and n_close:
                sid = _safe_int(current_session_id)
                if not sid:
                    opts = _list_session_options(uid)
                    return opts, None, "Selecciona una sesión para cerrarla."
                try:
                    db.close_session(int(sid))
                except Exception:
                    opts = _list_session_options(uid)
                    return opts, None, "No se pudo cerrar la sesión (DB)."
                opts = _list_session_options(uid)
                return opts, None, f"Sesión #{sid} cerrada."

            # Crear sesión nueva
            if trig.startswith("btn-new-session") and n_new:
                created_by = session.get("user_id")
                created_by = _safe_int(created_by) if created_by is not None else None
                sport = None
                try:
                    u = db.get_user_by_id(int(uid))
                    sport = (u or {}).get("sport")
                except Exception:
                    sport = None
                try:
                    sid = db.create_session(int(uid), created_by=created_by, sport=sport, notes=None)
                except Exception:
                    opts = _list_session_options(uid)
                    return opts, None, "No se pudo crear la sesión (DB)."
                opts = _list_session_options(uid)
                return opts, sid, f"Sesión #{sid} creada y activada."

            # Cambio de usuario (o carga): listar sesiones y autoseleccionar open si existe
            opts = _list_session_options(uid)
            chosen = None
            try:
                sessions = db.list_sessions(int(uid), limit=50) or []
                open_s = next((s for s in sessions if (s.get("status") == "open")), None)
                if open_s:
                    chosen = open_s.get("id")
            except Exception:
                chosen = None

            return opts, chosen, ""

        @app.callback(
            Output("ecg-file", "options"),
            Input("ecg-user", "value"),
            prevent_initial_call=True
        )
        def refresh_user_files(user_id):
            if not user_id:
                raise PreventUpdate
            uid = _safe_int(user_id)
            if not uid:
                raise PreventUpdate
            if not _has_sensor(uid, "ECG"):
                return []
            return _list_ecg_options(uid)

        @app.callback(
            Output("ecg-file", "value", allow_duplicate=True),
            Output("ecg-msg", "children", allow_duplicate=True),
            Input("btn-ecg-demo", "n_clicks"),
            State("ecg-user", "value"),
            State("signals-session", "value"),
            prevent_initial_call=True
        )
        def load_demo(n, user_id, session_id):
            if not user_id:
                return dash.no_update, "Selecciona usuario."
            uid = _safe_int(user_id)
            if not uid:
                return dash.no_update, "Usuario inválido."
            if not _has_sensor(uid, "ECG"):
                return dash.no_update, "Este deportista no tiene ECG asignado."

            os.makedirs(os.path.join("data", "ecg"), exist_ok=True)
            demo_path = os.path.join("data", "ecg", "ecg_example.csv")
            if not os.path.exists(demo_path):
                return dash.no_update, "No encuentro data/ecg/ecg_example.csv"

            sid = _safe_int(session_id)

            # (Opcional) Auto-crear sesión si aún no hay una seleccionada/abierta
            if not sid and hasattr(db, "ensure_open_session"):
                try:
                    actor_id = _safe_int(session.get("user_id"))
                    athlete = db.get_user_by_id(int(uid))
                    sport = athlete.get("sport") if athlete else None
                    sid = db.ensure_open_session(int(uid), created_by=actor_id, sport=sport)
                except Exception:
                    sid = None

            try:
                ecg_id = db.add_ecg_file(uid, "ecg_example.csv", 250, session_id=sid)
            except TypeError:
                ecg_id = db.add_ecg_file(uid, "ecg_example.csv", 250)

            return ecg_id, "ECG de ejemplo asociado."

        @app.callback(
            Output("ecg-file", "options", allow_duplicate=True),
            Output("ecg-file", "value", allow_duplicate=True),
            Output("ecg-msg", "children"),
            Input("ecg-upload", "contents"),
            State("ecg-upload", "filename"),
            State("ecg-user", "value"),
            State("signals-session", "value"),
            prevent_initial_call=True
        )
        def on_upload(content, filename, user_id, session_id):
            if not user_id:
                return dash.no_update, dash.no_update, "Selecciona usuario antes de subir."
            uid = _safe_int(user_id)
            if not uid:
                return dash.no_update, dash.no_update, "Usuario inválido."
            if not _has_sensor(uid, "ECG"):
                return dash.no_update, dash.no_update, "Este deportista no tiene ECG asignado."
            if not content:
                raise PreventUpdate

            try:
                data = _b64_to_bytes(content)
            except Exception:
                return dash.no_update, dash.no_update, "No se pudo leer el archivo (base64 inválido)."

            try:
                final_name = _save_unique(os.path.join("data", "ecg"), filename or "ecg.csv", data)
            except Exception:
                return dash.no_update, dash.no_update, "Error guardando el archivo en disco."

            save_path = os.path.join("data", "ecg", final_name)

            try:
                _, x, fs = read_ecg_csv(save_path, fs_default=250)
            except Exception as e:
                try:
                    os.remove(save_path)
                except Exception:
                    pass
                return dash.no_update, dash.no_update, f"Error leyendo el CSV: {e}"

            if x is None or len(x) == 0:
                try:
                    os.remove(save_path)
                except Exception:
                    pass
                return dash.no_update, dash.no_update, "El archivo no contiene datos de ECG válidos."

            sid = _safe_int(session_id)

            # (Opcional) Auto-crear sesión si aún no hay una seleccionada/abierta
            auto_note = ""
            if not sid and hasattr(db, "ensure_open_session"):
                try:
                    actor_id = _safe_int(session.get("user_id"))
                    athlete = db.get_user_by_id(int(uid))
                    sport = athlete.get("sport") if athlete else None
                    sid = db.ensure_open_session(int(uid), created_by=actor_id, sport=sport)
                    auto_note = " (Se creó sesión abierta automáticamente.)"
                except Exception:
                    sid = None

            try:
                ecg_id = db.add_ecg_file(uid, final_name, int(fs), session_id=sid)
            except TypeError:
                # DB legacy sin session_id
                ecg_id = db.add_ecg_file(uid, final_name, int(fs))

            opts = _list_ecg_options(uid)
            return opts, ecg_id, f"Archivo {final_name} guardado."

        @app.callback(
            Output("ecg-window", "max"),
            Output("ecg-window", "value"),
            Output("ecg-window", "marks"),
            Input("ecg-file", "value"),
            Input("ecg-winlen", "value"),
            State("ecg-user", "value"),
            prevent_initial_call=True
        )
        def sync_ecg_window(ecg_id, winlen, user_id):
            if not (user_id and ecg_id):
                raise PreventUpdate

            uid = _safe_int(user_id)
            fid = _safe_int(ecg_id)
            if not (uid and fid):
                raise PreventUpdate

            files = db.list_ecg_files(uid) or []
            row = next((f for f in files if int(f.get("id", -1)) == fid), None)
            if not row:
                raise PreventUpdate

            path = os.path.join("data", "ecg", row["filename"])
            if not os.path.exists(path):
                raise PreventUpdate

            try:
                t, x, fs = _cached_read_ecg_csv(path, fs_default=row.get("fs", 250))
            except Exception:
                raise PreventUpdate

            if t is None or len(t) < 2:
                raise PreventUpdate

            dur = float(t[-1] - t[0])
            if dur <= 0:
                raise PreventUpdate

            wl = int(winlen or 10)
            if wl <= 0:
                return dur, [0.0, dur], self._sparse_marks(dur)

            start = max(0.0, dur - float(wl))
            return dur, [start, dur], self._sparse_marks(dur)


        @app.callback(
            Output("ecg-graph", "figure"),
            Output("ecg-kpis", "children"),
            Input("ecg-file", "value"),
            Input("ecg-showr", "value"),
            Input("ecg-sens", "value"),
            Input("ecg-smooth", "value"),
            Input("ecg-window", "value"),
            Input("ecg-quality", "value"),
            State("ecg-user", "value"),
            prevent_initial_call=True
        )
        def render_ecg(ecg_id, showr_list, sens, smooth_ms, win_range, quality, user_id):
            if not (user_id and ecg_id):
                raise PreventUpdate

            uid = _safe_int(user_id)
            fid = _safe_int(ecg_id)
            if not (uid and fid):
                raise PreventUpdate

            if not _has_sensor(uid, "ECG"):
                raise PreventUpdate

            files = db.list_ecg_files(uid) or []
            row = next((f for f in files if int(f.get("id", -1)) == fid), None)
            if not row:
                raise PreventUpdate

            path = os.path.join("data", "ecg", row["filename"])
            if not os.path.exists(path):
                return go.Figure(), kpi_grid_ecg(0.0, 0.0, 0.0)

            try:
                t, x, fs = _cached_read_ecg_csv(path, fs_default=row.get("fs", 250))
            except Exception:
                return go.Figure(), kpi_grid_ecg(0.0, 0.0, 0.0)

            if x is None or len(x) == 0:
                return go.Figure(), kpi_grid_ecg(0.0, 0.0, 0.0)

            try:
                xs = smooth(x, int(smooth_ms or 0), fs) if smooth_ms and smooth_ms > 0 else x
            except Exception:
                xs = x

            show_r = "r" in (showr_list or [])
            peaks = None
            if show_r:
                try:
                    peaks = detect_r_peaks(xs, fs, sens or 0.6)
                except Exception:
                    peaks = None

            bpm, sdnn, rmssd = ecg_metrics_from_peaks(
                peaks if peaks is not None else np.array([]),
                fs
            )

            trig = ""
            try:
                if dash.callback_context.triggered:
                    trig = dash.callback_context.triggered[0]["prop_id"] or ""
            except Exception:
                trig = ""

            should_save = (trig.startswith("ecg-file.") or trig.startswith("ecg-showr."))
            if should_save and peaks is not None and len(peaks) > 1:
                try:
                    if hasattr(db, "save_ecg_metrics_latest"):
                        db.save_ecg_metrics_latest(fid, bpm, sdnn, rmssd, int(len(peaks)))
                    else:
                        db.save_ecg_metrics(fid, bpm, sdnn, rmssd, int(len(peaks)))
                except Exception:
                    pass

            # Ventana visible (solo visualización)
            try:
                if win_range and isinstance(win_range, (list, tuple)) and len(win_range) == 2:
                    t0, t1 = float(win_range[0]), float(win_range[1])
                else:
                    t0, t1 = 0.0, float(t[-1] - t[0])
            except Exception:
                t0, t1 = 0.0, float(t[-1] - t[0])

            t0 = max(0.0, t0)
            t1 = max(t0 + 1e-6, t1)

            i0 = int(np.searchsorted(t, t0, side="left"))
            i1 = int(np.searchsorted(t, t1, side="right"))
            i0 = max(0, min(i0, len(t) - 1))
            i1 = max(i0 + 1, min(i1, len(t)))

            t_win = t[i0:i1]
            x_win = xs[i0:i1]

            # Downsampling para render pro (no afecta métricas)
            q = (quality or "med").lower()
            max_pts = 8000 if q == "high" else (4000 if q == "med" else 2000)
            step = int(np.ceil(len(t_win) / max_pts)) if len(t_win) > max_pts else 1
            t_line = t_win[::step]
            x_line = x_win[::step]

            peaks_t = None
            peaks_y = None
            if show_r and peaks is not None and len(peaks) > 0:
                try:
                    pw = peaks[(peaks >= i0) & (peaks < i1)] - i0
                    peaks_t = t_win[pw]
                    peaks_y = x_win[pw]
                except Exception:
                    peaks_t, peaks_y = None, None

            fig = fig_ecg(t_line, x_line, peaks_t=peaks_t, peaks_y=peaks_y, title=row["filename"])
            kpis = kpi_grid_ecg(bpm, sdnn, rmssd)
            return fig, kpis

        @app.callback(
            Output("dl-png", "data"),
            Output("dl-png-clicks", "data"),
            Input("btn-dl-png", "n_clicks"),
            State("dl-png-clicks", "data"),
            State("ecg-graph", "figure"),
            prevent_initial_call=True
        )
        def download_png(n, last_n, fig_dict):
            if not n or (last_n is not None and n <= last_n):
                raise PreventUpdate

            fig = go.Figure(fig_dict)
            try:
                buf = fig.to_image(format="png", scale=2)
            except Exception:
                return dcc.send_string("Instala 'kaleido' para exportar PNG", "README.txt"), n

            return dcc.send_bytes(lambda b: b.write(buf), "ecg.png"), n

        @app.callback(
            Output("dl-peaks", "data"),
            Input("btn-dl-peaks", "n_clicks"),
            State("ecg-file", "value"),
            State("ecg-user", "value"),
            State("ecg-sens", "value"),
            State("ecg-smooth", "value"),
            prevent_initial_call=True
        )
        def download_peaks(n, ecg_id, user_id, sens, smooth_ms):
            if not (user_id and ecg_id):
                raise PreventUpdate

            uid = _safe_int(user_id)
            fid = _safe_int(ecg_id)
            if not (uid and fid):
                raise PreventUpdate

            if not _has_sensor(uid, "ECG"):
                raise PreventUpdate

            files = db.list_ecg_files(uid) or []
            row = next((f for f in files if int(f.get("id", -1)) == fid), None)
            if not row:
                raise PreventUpdate

            path = os.path.join("data", "ecg", row["filename"])
            t, x, fs = _cached_read_ecg_csv(path, fs_default=row.get("fs", 250))

            try:
                xs = smooth(x, int(smooth_ms or 0), fs) if smooth_ms and smooth_ms > 0 else x
            except Exception:
                xs = x

            peaks = detect_r_peaks(xs, fs, sens or 0.6)

            sio = io.StringIO()
            w = csv.writer(sio)
            w.writerow(["time_s", "value"])
            if peaks is not None and len(peaks) > 0:
                for idx in peaks:
                    w.writerow([f"{t[idx]:.6f}", f"{xs[idx]:.6f}"])
            csv_str = sio.getvalue()
            return dcc.send_bytes(lambda b: b.write(csv_str.encode("utf-8")), "r_peaks.csv")

        # --- IMU ---
        @app.callback(
            Output("imu-graph", "figure"),
            Output("imu-kpis", "children"),
            Output("imu-msg", "children"),
            Output("imu-meta", "data"),
            Output("imu-window", "max"),
            Output("imu-window", "value"),
            Output("imu-window", "marks"),
            Input("btn-imu-analyze", "n_clicks"),
            Input("imu-window", "value"),
            Input("imu-quality", "value"),
            Input("imu-winlen", "value"),
            State("imu-upload", "contents"),
            State("imu-upload", "filename"),
            State("imu-tabs", "value"),
            State("ecg-user", "value"),
            State("signals-session", "value"),
            State("imu-meta", "data"),
            prevent_initial_call=True,
        )
        def imu_pro(n_clicks, win_range, quality, winlen, content, filename, imu_kind, user_id, session_id, meta):
            # trigger detect
            trig = ""
            try:
                if dash.callback_context.triggered:
                    trig = dash.callback_context.triggered[0]["prop_id"] or ""
            except Exception:
                trig = ""

            if not user_id:
                return go.Figure(), [], "Selecciona deportista.", dash.no_update, dash.no_update, dash.no_update, dash.no_update

            uid = _safe_int(user_id)
            if not uid:
                return go.Figure(), [], "Usuario inválido.", dash.no_update, dash.no_update, dash.no_update, dash.no_update

            if not _has_sensor(uid, "IMU"):
                return go.Figure(), [], "Este deportista no tiene IMU asignado.", dash.no_update, dash.no_update, dash.no_update, dash.no_update

            # 1) Si se presiona analizar: guardamos el archivo y generamos meta + slider
            if trig.startswith("btn-imu-analyze"):
                if not n_clicks:
                    raise PreventUpdate
                if not content:
                    return go.Figure(), [], "Primero sube un archivo de IMU.", dash.no_update, dash.no_update, dash.no_update, dash.no_update

                try:
                    data = _b64_to_bytes(content)
                except Exception:
                    return go.Figure(), [], "No se pudo leer el archivo (base64 inválido).", dash.no_update, dash.no_update, dash.no_update, dash.no_update

                os.makedirs(os.path.join("data", "imu"), exist_ok=True)

                base_name = filename or "imu.csv"
                prefix = {"imu-arm": "arm_", "imu-leg": "leg_", "imu-head": "head_"}.get(imu_kind or "imu-arm", "arm_")

                try:
                    final_name = _save_unique(os.path.join("data", "imu"), prefix + base_name, data)
                except Exception:
                    return go.Figure(), [], "Error guardando el archivo en disco.", dash.no_update, dash.no_update, dash.no_update, dash.no_update

                save_path = os.path.join("data", "imu", final_name)

                t, mag, fs = read_imu_csv(save_path)
                if len(mag) == 0:
                    return go.Figure(), [], "El archivo no tiene datos válidos.", dash.no_update, dash.no_update, dash.no_update, dash.no_update

                n_hits, hits_per_min, mean_int, max_int, peaks = imu_metrics_from_mag(mag, t, fs)

                sid = _safe_int(session_id)
                # (Opcional) Auto-crear sesión si aún no hay una seleccionada/abierta
                if not sid and hasattr(db, "ensure_open_session"):
                    try:
                        actor_id = _safe_int(session.get("user_id"))
                        athlete = db.get_user_by_id(int(uid))
                        sport = athlete.get("sport") if athlete else None
                        sid = db.ensure_open_session(int(uid), created_by=actor_id, sport=sport)
                    except Exception:
                        sid = None

                # Guardado solo al ANALIZAR (no al mover sliders)
                try:
                    db.save_imu_metrics(uid, final_name, n_hits, hits_per_min, mean_int, max_int, session_id=sid)
                except TypeError:
                    try:
                        db.save_imu_metrics(uid, final_name, n_hits, hits_per_min, mean_int, max_int)
                    except Exception:
                        pass
                except Exception:
                    pass

                shown_name = filename or final_name
                if imu_kind == "imu-leg":
                    title = f"Patadas detectadas · {shown_name}"
                elif imu_kind == "imu-head":
                    title = f"Impactos en la cabeza · {shown_name}"
                else:
                    title = f"Golpes de brazo · {shown_name}"

                meta = {"path": save_path, "title": title, "uid": int(uid), "kind": (imu_kind or "imu-arm")}

                # Slider setup (como ECG Pro)
                dur = float(t[-1] - t[0]) if len(t) > 1 else 0.0
                dur = max(0.0, dur)
                wl = int(winlen or 10)
                if wl <= 0 or dur <= 0:
                    slider_value = [0.0, dur if dur > 0 else 10.0]
                else:
                    slider_value = [max(0.0, dur - float(wl)), dur]

                # usamos esa ventana para graficar al analizar
                win_range = slider_value
                slider_max = dur if dur > 0 else 10.0
                slider_marks = self._sparse_marks(slider_max)

                # Render (misma lógica que en sliders)
                msg = (f"Archivo {shown_name} analizado. "
                       f"Eventos: {n_hits} (eventos/min: {hits_per_min:.1f}).")

                # build fig
                t0, t1 = float(win_range[0]), float(win_range[1])
                t0 = max(0.0, t0); t1 = max(t0 + 1e-6, t1)
                i0 = int(np.searchsorted(t, t0, side="left"))
                i1 = int(np.searchsorted(t, t1, side="right"))
                i0 = max(0, min(i0, len(t) - 1))
                i1 = max(i0 + 1, min(i1, len(t)))

                t_win = t[i0:i1]
                mag_win = mag[i0:i1]

                q = (quality or "med").lower()
                max_pts = 8000 if q == "high" else (4000 if q == "med" else 2000)
                step = int(np.ceil(len(t_win) / max_pts)) if len(t_win) > max_pts else 1
                t_line = t_win[::step]
                mag_line = mag_win[::step]

                peaks_t = None
                peaks_y = None
                if peaks is not None and len(peaks) > 0:
                    try:
                        pw = peaks[(peaks >= i0) & (peaks < i1)]
                        peaks_t = t[pw]
                        peaks_y = mag[pw]
                    except Exception:
                        peaks_t, peaks_y = None, None

                thr = float(np.quantile(mag, 0.90)) if len(mag) > 0 else None
                fig = fig_imu(t_line, mag_line, peaks_t=peaks_t, peaks_y=peaks_y, thr=thr, title=title)
                kpis = kpi_grid_imu(n_hits, hits_per_min, mean_int, max_int)

                return fig, kpis, msg, meta, slider_max, slider_value, slider_marks

            # 2) Si no es analizar: necesitamos meta para re-render (ventana/calidad)
            if not meta or not meta.get("path"):
                raise PreventUpdate

            save_path = meta.get("path")
            title = meta.get("title") or "IMU"
            if not save_path or not os.path.exists(save_path):
                return go.Figure(), [], "No encuentro el archivo IMU (vuelve a analizar).", dash.no_update, dash.no_update, dash.no_update, dash.no_update

            # Re-leer y recalcular (mismo algoritmo; NO guardamos a DB aquí)
            t, mag, fs = read_imu_csv(save_path)
            if len(mag) == 0:
                return go.Figure(), [], "El archivo no tiene datos válidos.", dash.no_update, dash.no_update, dash.no_update, dash.no_update

            n_hits, hits_per_min, mean_int, max_int, peaks = imu_metrics_from_mag(mag, t, fs)

            # si cambia winlen, reseteamos ventana al final (como ECG)
            if trig.startswith("imu-winlen"):
                dur = float(t[-1] - t[0]) if len(t) > 1 else 0.0
                dur = max(0.0, dur)
                wl = int(winlen or 10)
                if wl <= 0 or dur <= 0:
                    win_range = [0.0, dur if dur > 0 else 10.0]
                else:
                    win_range = [max(0.0, dur - float(wl)), dur]
                slider_max = dur if dur > 0 else 10.0
                slider_value = win_range
                slider_marks = self._sparse_marks(slider_max)
            else:
                slider_max = dash.no_update
                slider_value = dash.no_update
                slider_marks = dash.no_update

            # Ventana (solo visual)
            dur = float(t[-1] - t[0]) if len(t) > 1 else 0.0
            dur = max(0.0, dur)
            try:
                if win_range and isinstance(win_range, (list, tuple)) and len(win_range) == 2:
                    t0, t1 = float(win_range[0]), float(win_range[1])
                else:
                    t0, t1 = 0.0, dur
            except Exception:
                t0, t1 = 0.0, dur

            t0 = max(0.0, t0)
            t1 = min(max(t0 + 1e-6, t1), dur if dur > 0 else t1)

            i0 = int(np.searchsorted(t, t0, side="left"))
            i1 = int(np.searchsorted(t, t1, side="right"))
            i0 = max(0, min(i0, len(t) - 1))
            i1 = max(i0 + 1, min(i1, len(t)))

            t_win = t[i0:i1]
            mag_win = mag[i0:i1]

            q = (quality or "med").lower()
            max_pts = 8000 if q == "high" else (4000 if q == "med" else 2000)
            step = int(np.ceil(len(t_win) / max_pts)) if len(t_win) > max_pts else 1
            t_line = t_win[::step]
            mag_line = mag_win[::step]

            peaks_t = None
            peaks_y = None
            if peaks is not None and len(peaks) > 0:
                try:
                    pw = peaks[(peaks >= i0) & (peaks < i1)]
                    peaks_t = t[pw]
                    peaks_y = mag[pw]
                except Exception:
                    peaks_t, peaks_y = None, None

            thr = float(np.quantile(mag, 0.90)) if len(mag) > 0 else None
            fig = fig_imu(t_line, mag_line, peaks_t=peaks_t, peaks_y=peaks_y, thr=thr, title=title)
            kpis = kpi_grid_imu(n_hits, hits_per_min, mean_int, max_int)

            return fig, kpis, dash.no_update, dash.no_update, slider_max, slider_value, slider_marks

# --- EMG ---
        @app.callback(
            Output("emg-graph", "figure"),
            Output("emg-kpis", "children"),
            Output("emg-msg", "children"),
            Output("emg-meta", "data"),
            Output("emg-window", "max"),
            Output("emg-window", "value"),
            Output("emg-window", "marks"),
            Input("btn-emg-analyze", "n_clicks"),
            Input("emg-window", "value"),
            Input("emg-quality", "value"),
            Input("emg-winlen", "value"),
            Input("emg-win", "value"),
            State("emg-upload", "contents"),
            State("emg-upload", "filename"),
            State("emg-tabs", "value"),
            State("ecg-user", "value"),
            State("signals-session", "value"),
            State("emg-meta", "data"),
            prevent_initial_call=True,
        )
        def emg_pro(n_clicks, win_range, quality, winlen, win_ms, content, filename, emg_kind, user_id, session_id, meta):
            trig = ""
            try:
                if dash.callback_context.triggered:
                    trig = dash.callback_context.triggered[0]["prop_id"] or ""
            except Exception:
                trig = ""

            if not user_id:
                return go.Figure(), [], "Selecciona deportista.", dash.no_update, dash.no_update, dash.no_update, dash.no_update

            uid = _safe_int(user_id)
            if not uid:
                return go.Figure(), [], "Usuario inválido.", dash.no_update, dash.no_update, dash.no_update, dash.no_update

            if not _has_sensor(uid, "EMG"):
                return go.Figure(), [], "Este deportista no tiene EMG asignado.", dash.no_update, dash.no_update, dash.no_update, dash.no_update

            # 1) Analizar: guardar, calcular métricas, preparar slider, guardar en DB
            if trig.startswith("btn-emg-analyze"):
                if not n_clicks:
                    raise PreventUpdate
                if not content:
                    return go.Figure(), [], "Primero sube un archivo de EMG.", dash.no_update, dash.no_update, dash.no_update, dash.no_update

                try:
                    data = _b64_to_bytes(content)
                except Exception:
                    return go.Figure(), [], "No se pudo leer el archivo (base64 inválido).", dash.no_update, dash.no_update, dash.no_update, dash.no_update

                os.makedirs(os.path.join("data", "emg"), exist_ok=True)

                base_name = filename or "emg.csv"
                prefix = {"emg-arm": "arm_", "emg-leg": "leg_"}.get(emg_kind or "emg-arm", "arm_")

                try:
                    final_name = _save_unique(os.path.join("data", "emg"), prefix + base_name, data)
                except Exception:
                    return go.Figure(), [], "Error guardando el archivo en disco.", dash.no_update, dash.no_update, dash.no_update, dash.no_update

                save_path = os.path.join("data", "emg", final_name)

                t, x, fs = read_emg_csv(save_path)
                if len(x) == 0:
                    return go.Figure(), [], "El archivo no tiene datos válidos.", dash.no_update, dash.no_update, dash.no_update, dash.no_update

                # Métricas globales (no cambian con la ventana)
                rms_global, peak, fatigue = emg_metrics(x, fs)

                sid = _safe_int(session_id)
                if not sid and hasattr(db, "ensure_open_session"):
                    try:
                        actor_id = _safe_int(session.get("user_id"))
                        athlete = db.get_user_by_id(int(uid))
                        sport = athlete.get("sport") if athlete else None
                        sid = db.ensure_open_session(int(uid), created_by=actor_id, sport=sport)
                    except Exception:
                        sid = None

                try:
                    db.save_emg_metrics(uid, final_name, rms_global, peak, fatigue, session_id=sid)
                except TypeError:
                    try:
                        db.save_emg_metrics(uid, final_name, rms_global, peak, fatigue)
                    except Exception:
                        pass
                except Exception:
                    pass

                shown_name = filename or final_name
                if emg_kind == "emg-leg":
                    title = f"EMG pierna · {shown_name}"
                    extra_msg = "Interpretación: activación y fatiga de pierna (patadas, desplazamientos)."
                else:
                    title = f"EMG brazo · {shown_name}"
                    extra_msg = "Interpretación: activación y fatiga de brazo (golpes, guardia)."

                # Meta para re-render (sin guardar otra vez)
                meta = {
                    "path": save_path,
                    "title": title,
                    "uid": int(uid),
                    "kind": (emg_kind or "emg-arm"),
                    "fs": int(fs),
                    "rms": float(rms_global),
                    "peak": float(peak),
                    "fatigue": float(fatigue),
                }

                # Slider setup
                dur = float(t[-1] - t[0]) if len(t) > 1 else 0.0
                dur = max(0.0, dur)
                wl = int(winlen or 10)
                if wl <= 0 or dur <= 0:
                    slider_value = [0.0, dur if dur > 0 else 10.0]
                else:
                    slider_value = [max(0.0, dur - float(wl)), dur]

                win_range = slider_value
                slider_max = dur if dur > 0 else 10.0
                slider_marks = self._sparse_marks(slider_max)

                # Render window (solo visual)
                t0, t1 = float(win_range[0]), float(win_range[1])
                t0 = max(0.0, t0); t1 = max(t0 + 1e-6, t1)
                i0 = int(np.searchsorted(t, t0, side="left"))
                i1 = int(np.searchsorted(t, t1, side="right"))
                i0 = max(0, min(i0, len(t) - 1))
                i1 = max(i0 + 1, min(i1, len(t)))

                t_win = t[i0:i1]
                x_win = x[i0:i1]

                rect = np.abs(x_win - np.mean(x_win))
                env = smooth(rect, win_ms=int(win_ms or 100), fs=fs)

                q = (quality or "med").lower()
                max_pts = 8000 if q == "high" else (4000 if q == "med" else 2000)
                step = int(np.ceil(len(t_win) / max_pts)) if len(t_win) > max_pts else 1
                t_line = t_win[::step]
                env_line = env[::step]

                thr = float(np.quantile(env, 0.90)) if len(env) > 0 else None
                fig = fig_emg(t_line, env_line, fs, thr=thr, title=title)
                kpis = kpi_grid_emg(rms_global, peak, fatigue)
                msg = (f"Archivo {shown_name} analizado. RMS: {rms_global:.3f}, "
                       f"pico: {peak:.3f}, fatiga: {fatigue:.1f}%. {extra_msg}")

                return fig, kpis, msg, meta, slider_max, slider_value, slider_marks

            # 2) Re-render: ventana / calidad / win_ms / winlen (sin guardar)
            if not meta or not meta.get("path"):
                raise PreventUpdate

            save_path = meta.get("path")
            title = meta.get("title") or "EMG"
            fs = int(meta.get("fs") or 1000)

            if not save_path or not os.path.exists(save_path):
                return go.Figure(), [], "No encuentro el archivo EMG (vuelve a analizar).", dash.no_update, dash.no_update, dash.no_update, dash.no_update

            t, x, _fs2 = read_emg_csv(save_path)
            if len(x) == 0:
                return go.Figure(), [], "El archivo no tiene datos válidos.", dash.no_update, dash.no_update, dash.no_update, dash.no_update

            # KPIs desde meta (no recalculamos pesado en sliders)
            rms_global = float(meta.get("rms") or 0.0)
            peak = float(meta.get("peak") or 0.0)
            fatigue = float(meta.get("fatigue") or 0.0)

            # Si cambia winlen, resetea ventana al final
            if trig.startswith("emg-winlen"):
                dur = float(t[-1] - t[0]) if len(t) > 1 else 0.0
                dur = max(0.0, dur)
                wl = int(winlen or 10)
                if wl <= 0 or dur <= 0:
                    win_range = [0.0, dur if dur > 0 else 10.0]
                else:
                    win_range = [max(0.0, dur - float(wl)), dur]
                slider_max = dur if dur > 0 else 10.0
                slider_value = win_range
                slider_marks = self._sparse_marks(slider_max)
            else:
                slider_max = dash.no_update
                slider_value = dash.no_update
                slider_marks = dash.no_update

            dur = float(t[-1] - t[0]) if len(t) > 1 else 0.0
            dur = max(0.0, dur)

            try:
                if win_range and isinstance(win_range, (list, tuple)) and len(win_range) == 2:
                    t0, t1 = float(win_range[0]), float(win_range[1])
                else:
                    t0, t1 = 0.0, dur
            except Exception:
                t0, t1 = 0.0, dur

            t0 = max(0.0, t0)
            t1 = min(max(t0 + 1e-6, t1), dur if dur > 0 else t1)

            i0 = int(np.searchsorted(t, t0, side="left"))
            i1 = int(np.searchsorted(t, t1, side="right"))
            i0 = max(0, min(i0, len(t) - 1))
            i1 = max(i0 + 1, min(i1, len(t)))

            t_win = t[i0:i1]
            x_win = x[i0:i1]

            rect = np.abs(x_win - np.mean(x_win))
            env = smooth(rect, win_ms=int(win_ms or 100), fs=fs)

            q = (quality or "med").lower()
            max_pts = 8000 if q == "high" else (4000 if q == "med" else 2000)
            step = int(np.ceil(len(t_win) / max_pts)) if len(t_win) > max_pts else 1
            t_line = t_win[::step]
            env_line = env[::step]

            thr = float(np.quantile(env, 0.90)) if len(env) > 0 else None
            fig = fig_emg(t_line, env_line, fs, thr=thr, title=title)
            kpis = kpi_grid_emg(rms_global, peak, fatigue)

            return fig, kpis, dash.no_update, dash.no_update, slider_max, slider_value, slider_marks

# --- RESP ---
        @app.callback(
            Output("resp-graph", "figure"),
            Output("resp-kpis", "children"),
            Output("resp-msg", "children"),
            Output("resp-meta", "data"),
            Output("resp-window", "max"),
            Output("resp-window", "value"),
            Output("resp-window", "marks"),
            Input("btn-resp-analyze", "n_clicks"),
            Input("resp-window", "value"),
            Input("resp-quality", "value"),
            Input("resp-winlen", "value"),
            Input("resp-sens", "value"),
            State("resp-upload", "contents"),
            State("resp-upload", "filename"),
            State("ecg-user", "value"),
            State("signals-session", "value"),
            State("resp-meta", "data"),
            prevent_initial_call=True
        )
        def resp_pro(n_clicks, win_range, quality, winlen, sens, content, filename, user_id, session_id, meta):
            trig = ""
            try:
                if dash.callback_context.triggered:
                    trig = dash.callback_context.triggered[0]["prop_id"] or ""
            except Exception:
                trig = ""

            if not user_id:
                return go.Figure(), [], "Selecciona deportista.", dash.no_update, dash.no_update, dash.no_update, dash.no_update

            uid = _safe_int(user_id)
            if not uid:
                return go.Figure(), [], "Usuario inválido.", dash.no_update, dash.no_update, dash.no_update, dash.no_update

            if not _has_sensor(uid, "RESP_BELT"):
                return go.Figure(), [], "Este deportista no tiene Respiración asignada.", dash.no_update, dash.no_update, dash.no_update, dash.no_update

            # 1) Analizar (guardar + DB)
            if trig.startswith("btn-resp-analyze"):
                if not n_clicks:
                    raise PreventUpdate
                if not content:
                    return go.Figure(), [], "Primero sube un archivo de respiración.", dash.no_update, dash.no_update, dash.no_update, dash.no_update

                try:
                    data = _b64_to_bytes(content)
                except Exception:
                    return go.Figure(), [], "No se pudo leer el archivo (base64 inválido).", dash.no_update, dash.no_update, dash.no_update, dash.no_update

                os.makedirs(os.path.join("data", "resp"), exist_ok=True)

                try:
                    final_name = _save_unique(os.path.join("data", "resp"), filename or "resp.csv", data)
                except Exception:
                    return go.Figure(), [], "Error guardando el archivo en disco.", dash.no_update, dash.no_update, dash.no_update, dash.no_update

                save_path = os.path.join("data", "resp", final_name)

                t, x, fs = read_resp_csv(save_path)
                if len(x) == 0:
                    return go.Figure(), [], "El archivo no tiene datos válidos.", dash.no_update, dash.no_update, dash.no_update, dash.no_update

                n_breaths, br_min, mean_period, peaks = resp_metrics(t, x, fs, sens=sens or 0.6)

                sid = _safe_int(session_id)
                if not sid and hasattr(db, "ensure_open_session"):
                    try:
                        actor_id = _safe_int(session.get("user_id"))
                        athlete = db.get_user_by_id(int(uid))
                        sport = athlete.get("sport") if athlete else None
                        sid = db.ensure_open_session(int(uid), created_by=actor_id, sport=sport)
                    except Exception:
                        sid = None

                try:
                    db.save_resp_metrics(uid, final_name, n_breaths, br_min, mean_period, session_id=sid)
                except TypeError:
                    try:
                        db.save_resp_metrics(uid, final_name, n_breaths, br_min, mean_period)
                    except Exception:
                        pass
                except Exception:
                    pass

                shown_name = filename or final_name
                title = f"Respiración · {shown_name}"

                # Preprocesado para render (filtrado/centrado)
                x0 = x - np.mean(x)
                env_full = smooth(x0, win_ms=250, fs=fs)

                meta = {
                    "path": save_path,
                    "title": title,
                    "uid": int(uid),
                    "fs": int(fs),
                    "sens": float(sens or 0.6),
                    "n": int(n_breaths),
                    "br": float(br_min),
                    "mp": float(mean_period),
                    "peaks": [int(p) for p in (peaks.tolist() if hasattr(peaks, "tolist") else (peaks if peaks is not None else []))],
                }

                # Slider setup
                dur = float(t[-1] - t[0]) if len(t) > 1 else 0.0
                dur = max(0.0, dur)
                wl = int(winlen or 30)
                if wl <= 0 or dur <= 0:
                    slider_value = [0.0, dur if dur > 0 else 10.0]
                else:
                    slider_value = [max(0.0, dur - float(wl)), dur]

                win_range = slider_value
                slider_max = dur if dur > 0 else 10.0
                slider_marks = self._sparse_marks(slider_max)

                # Ventana (solo visual)
                t0, t1 = float(win_range[0]), float(win_range[1])
                t0 = max(0.0, t0); t1 = max(t0 + 1e-6, t1)
                i0 = int(np.searchsorted(t, t0, side="left"))
                i1 = int(np.searchsorted(t, t1, side="right"))
                i0 = max(0, min(i0, len(t) - 1))
                i1 = max(i0 + 1, min(i1, len(t)))

                t_win = t[i0:i1]
                env_win = env_full[i0:i1]

                q = (quality or "med").lower()
                max_pts = 8000 if q == "high" else (4000 if q == "med" else 2000)
                step = int(np.ceil(len(t_win) / max_pts)) if len(t_win) > max_pts else 1
                t_line = t_win[::step]
                env_line = env_win[::step]

                peaks_t = None
                peaks_y = None
                if peaks is not None and len(peaks) > 0:
                    try:
                        pw = np.array(peaks, dtype=int)
                        pw = pw[(pw >= i0) & (pw < i1)]
                        peaks_t = t[pw]
                        peaks_y = env_full[pw]
                    except Exception:
                        peaks_t, peaks_y = None, None

                thr = float(np.quantile(env_full, 0.90)) if len(env_full) > 0 else None
                fig = fig_resp(t_line, env_line, peaks_t=peaks_t, peaks_y=peaks_y, thr=thr, title=title)
                kpis = kpi_grid_resp(n_breaths, br_min, mean_period)
                msg = f"Archivo {shown_name} analizado. Se detectaron {n_breaths} respiraciones."

                return fig, kpis, msg, meta, slider_max, slider_value, slider_marks

            # 2) Re-render (ventana / calidad / winlen / sens) — sin guardar
            if not meta or not meta.get("path"):
                raise PreventUpdate

            save_path = meta.get("path")
            title = meta.get("title") or "Respiración"

            if not save_path or not os.path.exists(save_path):
                return go.Figure(), [], "No encuentro el archivo de respiración (vuelve a analizar).", dash.no_update, dash.no_update, dash.no_update, dash.no_update

            t, x, fs = read_resp_csv(save_path)
            if len(x) == 0:
                return go.Figure(), [], "El archivo no tiene datos válidos.", dash.no_update, dash.no_update, dash.no_update, dash.no_update

            # Recalcula métricas si cambia sensibilidad (NO guardamos)
            n_breaths, br_min, mean_period, peaks = resp_metrics(t, x, fs, sens=sens or float(meta.get("sens") or 0.6))

            x0 = x - np.mean(x)
            env_full = smooth(x0, win_ms=250, fs=fs)

            # Si cambia winlen, resetea ventana al final
            if trig.startswith("resp-winlen"):
                dur = float(t[-1] - t[0]) if len(t) > 1 else 0.0
                dur = max(0.0, dur)
                wl = int(winlen or 30)
                if wl <= 0 or dur <= 0:
                    win_range = [0.0, dur if dur > 0 else 10.0]
                else:
                    win_range = [max(0.0, dur - float(wl)), dur]
                slider_max = dur if dur > 0 else 10.0
                slider_value = win_range
                slider_marks = self._sparse_marks(slider_max)
            else:
                slider_max = dash.no_update
                slider_value = dash.no_update
                slider_marks = dash.no_update

            dur = float(t[-1] - t[0]) if len(t) > 1 else 0.0
            dur = max(0.0, dur)

            try:
                if win_range and isinstance(win_range, (list, tuple)) and len(win_range) == 2:
                    t0, t1 = float(win_range[0]), float(win_range[1])
                else:
                    t0, t1 = 0.0, dur
            except Exception:
                t0, t1 = 0.0, dur

            t0 = max(0.0, t0)
            t1 = min(max(t0 + 1e-6, t1), dur if dur > 0 else t1)

            i0 = int(np.searchsorted(t, t0, side="left"))
            i1 = int(np.searchsorted(t, t1, side="right"))
            i0 = max(0, min(i0, len(t) - 1))
            i1 = max(i0 + 1, min(i1, len(t)))

            t_win = t[i0:i1]
            env_win = env_full[i0:i1]

            q = (quality or "med").lower()
            max_pts = 8000 if q == "high" else (4000 if q == "med" else 2000)
            step = int(np.ceil(len(t_win) / max_pts)) if len(t_win) > max_pts else 1
            t_line = t_win[::step]
            env_line = env_win[::step]

            peaks_t = None
            peaks_y = None
            if peaks is not None and len(peaks) > 0:
                try:
                    pw = np.array(peaks, dtype=int)
                    pw = pw[(pw >= i0) & (pw < i1)]
                    peaks_t = t[pw]
                    peaks_y = env_full[pw]
                except Exception:
                    peaks_t, peaks_y = None, None

            thr = float(np.quantile(env_full, 0.90)) if len(env_full) > 0 else None
            fig = fig_resp(t_line, env_line, peaks_t=peaks_t, peaks_y=peaks_y, thr=thr, title=title)
            kpis = kpi_grid_resp(n_breaths, br_min, mean_period)

            return fig, kpis, dash.no_update, dash.no_update, slider_max, slider_value, slider_marks
