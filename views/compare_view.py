# views/compare_view.py

import os
import io
import base64
import uuid
import re
from datetime import datetime

import numpy as np
import plotly.graph_objects as go

from ui_charts import apply_chart_style, graph_config

import dash
from dash import html, dcc, Input, Output, State
from dash.dash_table import DataTable
from dash.exceptions import PreventUpdate
from flask import session

# ---------------- ReportLab (opcional) ----------------
_REPORTLAB_OK = True
_REPORTLAB_ERR = ""
try:
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.lib import colors
    from reportlab.lib.utils import simpleSplit, ImageReader
except Exception as e:
    _REPORTLAB_OK = False
    _REPORTLAB_ERR = str(e)

# Reutilizamos helpers de la vista de señales
from .signals_view import (
    read_ecg_csv,
    detect_r_peaks,
    ecg_metrics_from_peaks,
    smooth,
    read_imu_csv,
    imu_metrics_from_mag,
    read_emg_csv,
    emg_metrics,
    read_resp_csv,
    resp_metrics,
)

_ALLOWED_EXTS = {".csv"}


# ================= Helpers base =================

def _safe_int(x):
    try:
        return int(x)
    except Exception:
        return None


def _safe_float(x, default=None):
    try:
        return float(x)
    except Exception:
        return default


def _fmt_pct(pct):
    """Evita el error NoneType.__format__."""
    try:
        return f"{float(pct):+.1f}%"
    except Exception:
        return "—"


def _sanitize_filename(filename: str, default: str = "file.csv") -> str:
    """
    Nombre seguro:
    - basename
    - solo [a-zA-Z0-9._-]
    - espacios -> _
    - fuerza extensión .csv
    - limita longitud
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


def _b64_to_bytes(content: str) -> bytes:
    if not content:
        raise ValueError("Contenido vacío")
    try:
        _, b64 = content.split(",", 1)
        return base64.b64decode(b64)
    except Exception as e:
        raise ValueError("Base64 inválido") from e


def _save_unique(dirpath: str, filename: str, data: bytes, prefix: str = "cmp_") -> str:
    """
    Guarda evitando sobrescritura: cmp_<uuid>_<filename_sanitizado>.csv
    Devuelve el nombre final.
    """
    os.makedirs(dirpath, exist_ok=True)
    safe = _sanitize_filename(filename or "file.csv")
    token = uuid.uuid4().hex[:8]
    final_name = f"{prefix}{token}_{safe}"
    full = os.path.join(dirpath, final_name)
    with open(full, "wb") as f:
        f.write(data)
    return final_name


def _session_label(s: dict):
    if not s:
        return "—"
    sid = s.get("id", "—")
    ts = (s.get("ts_start") or "")[:19].replace("T", " ")
    st = (s.get("status") or "—")
    return f"#{sid} · {ts} · {st}"


def _delta_and_pct(cur, prev):
    try:
        cur = float(cur)
        prev = float(prev)
    except Exception:
        return None, None
    d = cur - prev
    pct = (d / prev * 100.0) if abs(prev) > 1e-9 else None
    return d, pct


def _badge(text: str, kind: str = "neutral"):
    color = {
        "good": "#00f28a",
        "bad": "#ff6b6b",
        "neutral": "#e9eef6",
    }.get(kind, "#e9eef6")
    return html.Div(text, style={"color": color, "fontWeight": "bold", "marginTop": "6px"})


# Mapeo de aliases: en DB/UI a veces usamos códigos más específicos (IMU_GLOVE, EMG_ARM, etc.).
_SENSOR_ALIASES = {
    "ECG": {"ECG"},
    "IMU": {"IMU", "IMU_GLOVE", "IMU_HEAD", "IMU_WRIST"},
    "EMG": {"EMG", "EMG_ARM", "EMG_LEG"},
    "RESP_BELT": {"RESP_BELT", "RESP", "RESPIRATION", "RESP_CHEST"},
}

def _has_sensor(db, user_id: int, code: str) -> bool:
    """Chequea si el atleta tiene asignado el sensor, tolerando aliases."""
    try:
        codes = set(db.get_user_sensors(int(user_id)) or [])
        want = _SENSOR_ALIASES.get(code, {code})
        return bool(codes & want)
    except Exception:
        return False

def _latest_by_session(db, kind: str, session_id: int):
    """
    kind in {"imu","emg","resp"}
    Devuelve la fila más reciente por session_id (si existe).
    """
    if not session_id:
        return None

    fn_map = {
        "imu": "list_imu_metrics_by_session",
        "emg": "list_emg_metrics_by_session",
        "resp": "list_resp_metrics_by_session",
    }
    fn_name = fn_map.get(kind)
    if not fn_name or not hasattr(db, fn_name):
        return None

    try:
        rows = getattr(db, fn_name)(int(session_id)) or []
    except Exception:
        return None

    if not rows:
        return None

    def key(r):
        ts = r.get("ts")
        rid = r.get("id", 0)
        return (ts or "", rid)

    rows_sorted = sorted(rows, key=key, reverse=True)
    return rows_sorted[0]


def _ecg_row_for_session(db, user_id: int, session_id: int, label: str):
    """
    Toma el archivo ECG más reciente de esa sesión (si hay) y calcula métricas.
    """
    if not session_id:
        return None

    files = []
    if hasattr(db, "list_ecg_files_by_session"):
        try:
            files = db.list_ecg_files_by_session(int(session_id)) or []
        except Exception:
            files = []
    else:
        try:
            allf = db.list_ecg_files(int(user_id)) or []
        except Exception:
            allf = []
        files = [f for f in allf if _safe_int(f.get("session_id")) == int(session_id)]

    if not files:
        return None

    files = sorted(files, key=lambda r: _safe_int(r.get("id")) or 0, reverse=True)
    f = files[0]
    fname = f.get("filename")
    if not fname:
        return None

    path = os.path.join("data", "ecg", fname)
    if not os.path.exists(path):
        return None

    try:
        fs0 = int(f.get("fs") or 250)
    except Exception:
        fs0 = 250

    try:
        t, x, fs = read_ecg_csv(path, fs_default=fs0)
    except Exception:
        return None

    if x is None or len(x) < 5:
        return None

    try:
        xs = smooth(x, win_ms=40, fs=fs)
        peaks = detect_r_peaks(xs, fs, sens=0.6)
        bpm, sdnn, rmssd = ecg_metrics_from_peaks(peaks, fs)
    except Exception:
        bpm, sdnn, rmssd = 0.0, 0.0, 0.0
        peaks = np.array([], dtype=int)

    duration_s = float(t[-1] - t[0]) if len(t) > 1 else 0.0
    n_beats = int(len(peaks))

    return {
        "label": label,
        "filename": fname,
        "duration_s": round(duration_s, 1),
        "n_beats": n_beats,
        "bpm": int(round(bpm)) if bpm > 0 else 0,
        "sdnn_ms": int(round(sdnn)) if sdnn > 0 else 0,
        "rmssd_ms": int(round(rmssd)) if rmssd > 0 else 0,
    }


# ================= Heurísticas (badges) =================

def _ecg_recovery_badge(ecg_cur: dict, ecg_prev: dict):
    """
    Heurística simple:
    - mejor si RMSSD ↑ y SDNN ↑ y BPM ↓
    """
    if not (ecg_cur and ecg_prev):
        return None

    bpm_d, _ = _delta_and_pct(ecg_cur.get("bpm", 0), ecg_prev.get("bpm", 0))
    sdnn_d, _ = _delta_and_pct(ecg_cur.get("sdnn_ms", 0), ecg_prev.get("sdnn_ms", 0))
    rmssd_d, _ = _delta_and_pct(ecg_cur.get("rmssd_ms", 0), ecg_prev.get("rmssd_ms", 0))

    score = 0
    if bpm_d is not None and bpm_d < 0:
        score += 1
    if sdnn_d is not None and sdnn_d > 0:
        score += 1
    if rmssd_d is not None and rmssd_d > 0:
        score += 1

    if score >= 2:
        return _badge("🟢 Recuperación/HRV mejor vs sesión anterior", "good")
    if score == 1:
        return _badge("🟡 HRV mixto vs sesión anterior (checa contexto: sueño, carga, estrés)", "neutral")
    return _badge("🔴 HRV peor vs sesión anterior (posible fatiga / poca recuperación)", "bad")


def _load_badge(cur: float, prev: float, label="Carga"):
    d, pct = _delta_and_pct(cur, prev)
    if d is None:
        return None

    pct_s = _fmt_pct(pct)
    prevv = _safe_float(prev, 0.0) or 0.0
    base = abs(prevv) if abs(prevv) > 1e-9 else max(1.0, abs(_safe_float(cur, 0.0) or 0.0))
    thr = 0.01 * base

    if d <= -thr:
        return _badge(f"🟢 {label} bajó ({pct_s})", "good")
    if d >= thr:
        return _badge(f"🔴 {label} subió ({pct_s})", "bad")
    return _badge(f"🟡 {label} estable ({pct_s})", "neutral")


def _fatigue_badge(cur_fat: float, prev_fat: float):
    d, pct = _delta_and_pct(cur_fat, prev_fat)
    if d is None:
        return None

    pct_s = _fmt_pct(pct)

    # fatiga menor = mejor
    if d < -0.5:
        return _badge(f"🟢 Fatiga EMG menor ({pct_s})", "good")
    if d > 0.5:
        return _badge(f"🔴 Fatiga EMG mayor ({pct_s})", "bad")
    return _badge(f"🟡 Fatiga EMG similar ({pct_s})", "neutral")


def _resp_badge(cur_br: float, prev_br: float):
    d, pct = _delta_and_pct(cur_br, prev_br)
    if d is None:
        return None

    pct_s = _fmt_pct(pct)

    # respiraciones/min menor = generalmente mejor (menos estrés)
    if d < -0.3:
        return _badge(f"🟢 Respiración más calmada ({pct_s})", "good")
    if d > 0.3:
        return _badge(f"🔴 Respiración más alta ({pct_s})", "bad")
    return _badge(f"🟡 Respiración similar ({pct_s})", "neutral")


def _overall_summary(ecg_b=None, imu_b=None, emg_b=None, resp_b=None):
    texts = []
    for b in [ecg_b, imu_b, emg_b, resp_b]:
        if isinstance(b, html.Div):
            try:
                texts.append(str(b.children))
            except Exception:
                pass

    if not texts:
        return "No hay suficientes señales para una conclusión completa. Sube o guarda métricas por sesión."

    red = sum(1 for t in texts if "🔴" in t)
    green = sum(1 for t in texts if "🟢" in t)

    if green >= 2 and red == 0:
        return "🟢 Sesión con tendencia positiva vs la anterior (buena recuperación y control de carga)."
    if red >= 2:
        return "🔴 Sesión con señales de fatiga/estrés vs la anterior (recomendable bajar carga y priorizar recuperación)."
    return "🟡 Sesión mixta: algunos indicadores mejoran y otros empeoran (interpreta según el tipo de entrenamiento)."


def _recommendations(ecg_b=None, imu_b=None, emg_b=None, resp_b=None):
    recs = []

    def txt(div):
        try:
            return str(div.children)
        except Exception:
            return ""

    t_ecg = txt(ecg_b)
    t_imu = txt(imu_b)
    t_emg = txt(emg_b)
    t_resp = txt(resp_b)

    if "🔴" in t_ecg:
        recs.append("Baja intensidad 24–48h y prioriza sueño/hidratación. Si se repite, revisa carga semanal.")
    if "🔴" in t_imu:
        recs.append("Carga externa alta: reduce volumen de sparring o trabaja técnica con menor impacto.")
    if "🔴" in t_emg:
        recs.append("Fatiga muscular elevada: mete movilidad + fuerza ligera; evita picos de intensidad consecutivos.")
    if "🔴" in t_resp:
        recs.append("Respiración alta: incluye 5–8 min de respiración nasal/box breathing post-sesión.")

    if not recs:
        recs.append("Mantén el plan: progresión controlada y monitoreo continuo por sesión.")
        recs.append("Registra bienestar para contexto (sueño, estrés, DOMS).")

    return recs



def _compare_bar_fig(title: str, x_labels, series, y_title: str = "Valor"):
    """Crea una figura de barras comparativa con estilo PowerSync (desktop)."""
    fig = go.Figure()
    for s in series:
        # s: dict(name=..., y=[...])
        fig.add_trace(go.Bar(x=x_labels, y=s.get("y", []), name=s.get("name", "")))
    fig.update_layout(barmode="group")
    apply_chart_style(fig, title=title, x_title="Sesión", y_title=y_title, height=380)
    return fig

# ================= Vista =================

class CompareView:
    """
    Vista de 'Comparar':

    1) Comparación por sesión (seleccionada vs anterior) usando session_id:
       - ECG/HRV, IMU, EMG, RESP
       - Conclusión + recomendaciones
       - Reporte PDF con gráficas

    2) Comparación clásica ECG por archivos del deportista

    3) Comparación por multi-upload (IMU/EMG/Resp) sin DB
    """

    def __init__(self, app: dash.Dash, db, sensors_module):
        self.app = app
        self.db = db
        self.S = sensors_module
        self._register_callbacks()

    # ====================== LAYOUT ======================

    def layout(self):
        if not session.get("user_id"):
            return html.Div("Inicia sesión para ver esta página.")

        role = (session.get("role") or "no autenticado")
        uid = session.get("user_id")

        # Qué deportistas puede ver
        if role == "coach" and uid:
            athletes = self.db.list_athletes_for_coach(int(uid))
        elif role == "deportista" and uid:
            u = self.db.get_user_by_id(int(uid))
            athletes = [u] if u and u.get("role") == "deportista" else []
        else:
            athletes = [
                u for u in self.db.list_users()
                if (u.get("role", "deportista") == "deportista")
            ]

        options_users = [
            {"label": f"{u['name']} · {u.get('sport', '-')}", "value": u["id"]}
            for u in athletes
        ]
        default_user = options_users[0]["value"] if options_users else None

        # Selector de deportista
        if role == "deportista":
            user_selector = html.Div([
                html.Label("Deportista"),
                dcc.Dropdown(
                    id="cmp-user",
                    options=options_users,
                    value=default_user,
                    disabled=True,
                )
            ], className="filter-item")
        else:
            user_selector = html.Div([
                html.Label("Deportista"),
                dcc.Dropdown(
                    id="cmp-user",
                    options=options_users,
                    value=default_user,
                    placeholder="Selecciona deportista..."
                )
            ], className="filter-item")

        # Selector de sesión (comparación seleccionada vs anterior)
        pdf_note = None
        if not _REPORTLAB_OK:
            pdf_note = html.Div(
                f"PDF deshabilitado: instala reportlab en tu venv (python -m pip install reportlab). "
                f"Detalle: {_REPORTLAB_ERR}",
                className="muted",
                style={"marginTop": "6px", "opacity": 0.8, "color": "#FFB4B4"},
            )

        session_selector = html.Div(
            className="filter-item",
            children=[
                html.Label("Sesión seleccionada (se compara vs la anterior)"),
                dcc.Dropdown(
                    id="cmp-session",
                    options=[],
                    placeholder="Selecciona una sesión...",
                    clearable=True,
                ),
                html.Div(id="cmp-prev-label", className="muted", style={"marginTop": "6px", "opacity": 0.85}),
                html.Div(style={"marginTop": "10px"}, children=[
                    html.Button(
                        "Descargar informe (PDF)",
                        id="btn-cmp-report",
                        className="btn btn-primary",
                        disabled=(not _REPORTLAB_OK),
                    ),
                    html.Span(id="cmp-report-msg", style={"marginLeft": "10px", "color": "#FFB4B4"}),
                    dcc.Download(id="cmp-report-dl"),
                ]),
                pdf_note,
                dcc.Store(id="cmp-session-ids", data={"cur": None, "prev": None}),
            ],
        )

        # ----------- styles ----------
        def _sess_table_style():
            return dict(
                sort_action="native",
                page_size=10,
                fixed_rows={"headers": True},
                style_table={"overflowX": "auto", "overflowY": "auto", "maxHeight": "360px"},
                style_cell={
                    "backgroundColor": "#0f131a",
                    "color": "#e9eef6",
                    "border": "1px solid #232a36",
                    "padding": "8px",
                    "fontSize": "13px",
                    "whiteSpace": "nowrap",
                    "textOverflow": "ellipsis",
                    "maxWidth": "280px",
                },
                style_header={
                    "backgroundColor": "#151a21",
                    "fontWeight": "bold",
                    "border": "1px solid #232a36",
                },
            )

        # ----------- BLOQUE: Sesión seleccionada vs anterior -----------
        session_compare_block = html.Div(
            style={"marginTop": "16px"},
            children=[
                html.H3("Comparación por sesión: seleccionada vs anterior"),
                html.Small(
                    "Usa el session_id guardado en DB para comparar la sesión seleccionada contra la inmediatamente anterior.",
                    style={"opacity": 0.8},
                ),
                html.Br(), html.Br(),

                html.Div(id="cmp-overall", style={"marginBottom": "10px", "fontWeight": "bold"}),
                html.Div([
                    html.Div("Recomendaciones:", style={"fontWeight": "bold", "marginTop": "8px"}),
                    html.Ul(id="cmp-recs", style={"marginTop": "6px"})
                ], style={"marginBottom": "18px"}),

                html.H4("ECG / HRV (sesión vs anterior)"),
                                html.Div(
                    className="dt-pro",
                    children=DataTable(
                    id="cmp-ecg-sess-table",
columns=[
                        {"name": "Sesión", "id": "label"},
                        {"name": "Archivo", "id": "filename"},
                        {"name": "Duración (s)", "id": "duration_s"},
                        {"name": "Latidos", "id": "n_beats"},
                        {"name": "BPM", "id": "bpm"},
                        {"name": "SDNN (ms)", "id": "sdnn_ms"},
                        {"name": "RMSSD (ms)", "id": "rmssd_ms"},
                    ],
                    data=[],
                    **_sess_table_style()
                ),
                ),
                html.Div(id="cmp-ecg-sess-badge"),
                dcc.Graph(id="cmp-ecg-sess-bars", figure=go.Figure(), config=graph_config(), style={"height": "380px", "width": "100%"}),
                html.Hr(style={"marginTop": "22px"}),

                html.H4("IMU (sesión vs anterior)"),
                                html.Div(
                    className="dt-pro",
                    children=DataTable(
                    id="cmp-imu-sess-table",
columns=[
                        {"name": "Sesión", "id": "label"},
                        {"name": "Archivo", "id": "filename"},
                        {"name": "Golpes", "id": "n_hits"},
                        {"name": "Golpes/min", "id": "hits_per_min"},
                        {"name": "Int media (g)", "id": "mean_int_g"},
                        {"name": "Int máx (g)", "id": "max_int_g"},
                        {"name": "Índice carga", "id": "load_index"},
                    ],
                    data=[],
                    **_sess_table_style()
                ),
                ),
                html.Div(id="cmp-imu-sess-badge"),
                dcc.Graph(id="cmp-imu-sess-bars", figure=go.Figure(), config=graph_config(), style={"height": "380px", "width": "100%"}),
                html.Hr(style={"marginTop": "22px"}),

                html.H4("EMG (sesión vs anterior)"),
                                html.Div(
                    className="dt-pro",
                    children=DataTable(
                    id="cmp-emg-sess-table",
columns=[
                        {"name": "Sesión", "id": "label"},
                        {"name": "Archivo", "id": "filename"},
                        {"name": "RMS", "id": "rms"},
                        {"name": "Pico", "id": "peak"},
                        {"name": "Fatiga (%)", "id": "fatigue"},
                    ],
                    data=[],
                    **_sess_table_style()
                ),
                ),
                html.Div(id="cmp-emg-sess-badge"),
                dcc.Graph(id="cmp-emg-sess-bars", figure=go.Figure(), config=graph_config(), style={"height": "380px", "width": "100%"}),
                html.Hr(style={"marginTop": "22px"}),

                html.H4("Respiración (sesión vs anterior)"),
                                html.Div(
                    className="dt-pro",
                    children=DataTable(
                    id="cmp-resp-sess-table",
columns=[
                        {"name": "Sesión", "id": "label"},
                        {"name": "Archivo", "id": "filename"},
                        {"name": "Respiraciones", "id": "n_breaths"},
                        {"name": "Resp/min", "id": "br_min"},
                        {"name": "Periodo medio (s)", "id": "mean_period"},
                        {"name": "Índice estrés", "id": "stress_index"},
                    ],
                    data=[],
                    **_sess_table_style()
                ),
                ),
                html.Div(id="cmp-resp-sess-badge"),
                dcc.Graph(id="cmp-resp-sess-bars", figure=go.Figure(), config=graph_config(), style={"height": "380px", "width": "100%"}),
            ],
        )

        # ---------- BLOQUE ECG clásico ----------
        ecg_block = html.Div(
            style={"marginTop": "24px"},
            children=[
                html.H3("ECG / HRV (comparación clásica por archivos)"),
                html.Small(
                    "Selecciona un deportista para ver todos sus archivos ECG guardados y comparar métricas.",
                    style={"opacity": 0.8},
                ),
                html.Br(), html.Br(),
                html.H4("Archivos ECG del deportista"),
                                html.Div(
                    className="dt-pro",
                    children=DataTable(
                    id="cmp-ecg-table",
columns=[
                        {"name": "ID", "id": "id"},
                        {"name": "Archivo", "id": "filename"},
                        {"name": "Duración (s)", "id": "duration_s"},
                        {"name": "Latidos", "id": "n_beats"},
                        {"name": "BPM", "id": "bpm"},
                        {"name": "SDNN (ms)", "id": "sdnn_ms"},
                        {"name": "RMSSD (ms)", "id": "rmssd_ms"},
                    ],
                    data=[],
                    row_selectable="multi",
                    selected_rows=[],
                    sort_action="native",
                    page_size=10,
                    style_table={"overflowX": "auto"},
                    style_cell={
                        "backgroundColor": "#0f131a",
                        "color": "#e9eef6",
                        "border": "1px solid #232a36",
                        "padding": "8px",
                        "fontSize": "13px",
                        "whiteSpace": "nowrap",
                        "textOverflow": "ellipsis",
                        "maxWidth": "280px",
                    },
                    style_header={
                        "backgroundColor": "#151a21",
                        "fontWeight": "bold",
                        "border": "1px solid #232a36",
                    },
                ),
                ),
                html.Div(
                    "Selecciona 2–4 filas para comparar (si seleccionas más, se toman las primeras 4).",
                    className="muted",
                    style={"marginTop": "6px", "fontSize": "13px", "opacity": 0.8},
                ),
                html.Br(),
                dcc.Graph(id="cmp-ecg-bars", figure=go.Figure(), config=graph_config(), style={"height": "420px", "width": "100%"}),
            ],
        )

        # ---------- BLOQUES multi-upload ----------
        imu_block = html.Div(
            style={"marginTop": "24px"},
            children=[
                html.H3("IMU (multi-upload por archivos)"),
                html.Small(
                    "Sube varios archivos IMU (time,ax,ay,az) para comparar golpes/min e intensidad.",
                    style={"opacity": 0.8},
                ),
                html.Br(), html.Br(),
                dcc.Upload(
                    id="cmp-imu-upload",
                    children=html.Div("Arrastra o elige uno o varios archivos IMU (.csv)"),
                    multiple=True,
                    style={"padding": "12px", "border": "1px dashed #2b3a52", "borderRadius": "10px"},
                ),
                html.Br(),
                                html.Div(
                    className="dt-pro",
                    children=DataTable(
                    id="cmp-imu-table",
columns=[
                        {"name": "Sesión", "id": "session"},
                        {"name": "Golpes", "id": "n_hits"},
                        {"name": "Golpes/min", "id": "hits_per_min"},
                        {"name": "Intensidad media (g)", "id": "mean_int_g"},
                        {"name": "Intensidad máx (g)", "id": "max_int_g"},
                    ],
                    data=[],
                    sort_action="native",
                    page_size=10,
                    style_table={"overflowX": "auto"},
                    style_cell={
                        "backgroundColor": "#0f131a",
                        "color": "#e9eef6",
                        "border": "1px solid #232a36",
                        "padding": "8px",
                        "fontSize": "13px",
                        "whiteSpace": "nowrap",
                    },
                    style_header={"backgroundColor": "#151a21", "fontWeight": "bold", "border": "1px solid #232a36"},
                ),
                ),
                html.Br(),
                dcc.Graph(id="cmp-imu-bars", figure=go.Figure(), config=graph_config(), style={"height": "420px", "width": "100%"}),
            ],
        )

        emg_block = html.Div(
            style={"marginTop": "24px"},
            children=[
                html.H3("EMG (multi-upload por archivos)"),
                html.Small(
                    "Sube varios archivos EMG ('time,emg' o 'time,ch1') para comparar RMS, pico y fatiga.",
                    style={"opacity": 0.8},
                ),
                html.Br(), html.Br(),
                dcc.Upload(
                    id="cmp-emg-upload",
                    children=html.Div("Arrastra o elige uno o varios archivos EMG (.csv)"),
                    multiple=True,
                    style={"padding": "12px", "border": "1px dashed #2b3a52", "borderRadius": "10px"},
                ),
                html.Br(),
                                html.Div(
                    className="dt-pro",
                    children=DataTable(
                    id="cmp-emg-table",
columns=[
                        {"name": "Sesión", "id": "session"},
                        {"name": "RMS global", "id": "rms"},
                        {"name": "Pico abs", "id": "peak"},
                        {"name": "Fatiga (%)", "id": "fatigue"},
                    ],
                    data=[],
                    sort_action="native",
                    page_size=10,
                    style_table={"overflowX": "auto"},
                    style_cell={
                        "backgroundColor": "#0f131a",
                        "color": "#e9eef6",
                        "border": "1px solid #232a36",
                        "padding": "8px",
                        "fontSize": "13px",
                        "whiteSpace": "nowrap",
                    },
                    style_header={"backgroundColor": "#151a21", "fontWeight": "bold", "border": "1px solid #232a36"},
                ),
                ),
                html.Br(),
                dcc.Graph(id="cmp-emg-bars", figure=go.Figure(), config=graph_config(), style={"height": "420px", "width": "100%"}),
            ],
        )

        resp_block = html.Div(
            style={"marginTop": "24px", "marginBottom": "40px"},
            children=[
                html.H3("Respiración (multi-upload por archivos)"),
                html.Small(
                    "Sube varios archivos de banda respiratoria ('time,resp') para comparar respiraciones/min y periodo medio.",
                    style={"opacity": 0.8},
                ),
                html.Br(), html.Br(),
                dcc.Upload(
                    id="cmp-resp-upload",
                    children=html.Div("Arrastra o elige uno o varios archivos de respiración (.csv)"),
                    multiple=True,
                    style={"padding": "12px", "border": "1px dashed #2b3a52", "borderRadius": "10px"},
                ),
                html.Br(),
                                html.Div(
                    className="dt-pro",
                    children=DataTable(
                    id="cmp-resp-table",
columns=[
                        {"name": "Sesión", "id": "session"},
                        {"name": "Respiraciones", "id": "n_breaths"},
                        {"name": "Resp/min", "id": "br_min"},
                        {"name": "Periodo medio (s)", "id": "mean_period"},
                    ],
                    data=[],
                    sort_action="native",
                    page_size=10,
                    style_table={"overflowX": "auto"},
                    style_cell={
                        "backgroundColor": "#0f131a",
                        "color": "#e9eef6",
                        "border": "1px solid #232a36",
                        "padding": "8px",
                        "fontSize": "13px",
                        "whiteSpace": "nowrap",
                    },
                    style_header={"backgroundColor": "#151a21", "fontWeight": "bold", "border": "1px solid #232a36"},
                ),
                ),
                html.Br(),
                dcc.Graph(id="cmp-resp-bars", figure=go.Figure(), config=graph_config(), style={"height": "420px", "width": "100%"}),
            ],
        )

        return html.Div(
            [
                html.H2("Comparar", className="page-title"),
                html.Small(
                    "Comparación por sesión (seleccionada vs anterior), comparación clásica ECG y comparación por archivos (multi-upload).",
                    style={"opacity": 0.8},
                ),
                html.Hr(),
                html.Div(className="filters-bar filters-bar--2", children=[
                    user_selector,
                    session_selector,
                ]),
                session_compare_block,
                html.Hr(style={"marginTop": "32px"}),
                ecg_block,
                html.Hr(style={"marginTop": "32px"}),
                imu_block,
                html.Hr(style={"marginTop": "32px"}),
                emg_block,
                html.Hr(style={"marginTop": "32px"}),
                resp_block,
            ]
        )

    # ====================== CALLBACKS ======================

    def _register_callbacks(self):
        app = self.app
        db = self.db

        # ---------- Session selector: options + prev ----------
        @app.callback(
            Output("cmp-session", "options"),
            Output("cmp-session", "value"),
            Output("cmp-session-ids", "data"),
            Output("cmp-prev-label", "children"),
            Input("cmp-user", "value"),
            prevent_initial_call=False,
        )
        def load_sessions_for_user(user_id):
            uid = _safe_int(user_id)
            if not uid:
                return [], None, {"cur": None, "prev": None}, "Selecciona un deportista."

            if not hasattr(db, "list_sessions"):
                return [], None, {"cur": None, "prev": None}, "Tu DB todavía no expone sesiones (list_sessions)."

            try:
                sessions = db.list_sessions(int(uid), limit=50) or []
            except Exception:
                sessions = []

            # ordenar: más reciente primero (ts_start si existe, si no id)
            def _k(s):
                ts = (s.get("ts_start") or "")
                sid = _safe_int(s.get("id")) or 0
                return (ts, sid)

            sessions = sorted(sessions, key=_k, reverse=True)

            opts = []
            for s in sessions:
                sid = s.get("id")
                if sid is None:
                    continue
                ts = (s.get("ts_start") or "")[:19].replace("T", " ")
                st = (s.get("status") or "—")
                opts.append({"label": f"#{sid} · {ts} · {st}", "value": sid})

            chosen = opts[0]["value"] if opts else None

            prev_id = None
            prev_label = "Sesión anterior: —"

            if chosen:
                # 1) si existe método dedicado
                if hasattr(db, "get_previous_session"):
                    try:
                        ps = db.get_previous_session(int(uid), int(chosen))
                    except Exception:
                        ps = None
                    prev_id = ps.get("id") if ps else None
                    prev_label = f"Sesión anterior: {_session_label(ps) if ps else '—'}"
                else:
                    # 2) fallback: buscamos en la lista ordenada
                    ids = [(_safe_int(s.get("id")) or 0) for s in sessions]
                    try:
                        idx = ids.index(int(chosen))
                        if idx + 1 < len(sessions):
                            ps = sessions[idx + 1]
                            prev_id = _safe_int(ps.get("id"))
                            prev_label = f"Sesión anterior: {_session_label(ps) if ps else '—'}"
                    except Exception:
                        prev_id = None
                        prev_label = "Sesión anterior: —"

            data = {"cur": chosen, "prev": prev_id}
            return opts, chosen, data, prev_label

        # ---------- ECG (sesión vs anterior) ----------
        @app.callback(
            Output("cmp-ecg-sess-table", "data"),
            Output("cmp-ecg-sess-bars", "figure"),
            Output("cmp-ecg-sess-badge", "children"),
            Input("cmp-session-ids", "data"),
            State("cmp-user", "value"),
            prevent_initial_call=False,
        )
        def ecg_session_compare(store, user_id):
            uid = _safe_int(user_id)
            if not uid or not store:
                return [], go.Figure(), None

            cur_id = _safe_int(store.get("cur"))
            prev_id = _safe_int(store.get("prev"))
            if not cur_id:
                return [], go.Figure(), None

            if not _has_sensor(db, uid, "ECG"):
                return [], go.Figure(), _badge("ECG no asignado a este deportista.", "neutral")

            cur = _ecg_row_for_session(db, uid, cur_id, "Seleccionada")
            prev = _ecg_row_for_session(db, uid, prev_id, "Anterior") if prev_id else None

            rows = []
            if cur:
                rows.append(cur)
            if prev:
                rows.append(prev)

            fig = go.Figure()

            if cur and prev:
                x = ["Seleccionada", "Anterior"]
                fig = _compare_bar_fig(
                    "ECG/HRV: seleccionada vs anterior",
                    x,
                    series=[
                        {"name": "BPM", "y": [cur["bpm"], prev["bpm"]]},
                        {"name": "SDNN (ms)", "y": [cur["sdnn_ms"], prev["sdnn_ms"]]},
                        {"name": "RMSSD (ms)", "y": [cur["rmssd_ms"], prev["rmssd_ms"]]},
                    ],
                    y_title="Valor",
                )
                badge = _ecg_recovery_badge(cur, prev)
            else:
                apply_chart_style(fig, title="ECG/HRV: faltan datos para comparar", x_title="Sesión", y_title="Valor", height=380)
                badge = _badge("No hay suficiente ECG guardado en ambas sesiones para comparar.", "neutral")

            return rows, fig, badge

        # ---------- IMU (sesión vs anterior) ----------
        @app.callback(
            Output("cmp-imu-sess-table", "data"),
            Output("cmp-imu-sess-bars", "figure"),
            Output("cmp-imu-sess-badge", "children"),
            Input("cmp-session-ids", "data"),
            State("cmp-user", "value"),
            prevent_initial_call=False,
        )
        def imu_session_compare(store, user_id):
            uid = _safe_int(user_id)
            if not uid or not store:
                return [], go.Figure(), None

            cur_id = _safe_int(store.get("cur"))
            prev_id = _safe_int(store.get("prev"))
            if not cur_id:
                return [], go.Figure(), None

            if not _has_sensor(db, uid, "IMU"):
                return [], go.Figure(), _badge("IMU no asignado a este deportista.", "neutral")

            cur_row = _latest_by_session(db, "imu", cur_id)
            prev_row = _latest_by_session(db, "imu", prev_id) if prev_id else None

            rows = []
            cur = prev = None

            if cur_row:
                hpm = float(cur_row.get("hits_per_min", 0) or 0)
                mi = float(cur_row.get("mean_int_g", 0) or 0)
                cur = {
                    "label": "Seleccionada",
                    "filename": cur_row.get("filename", "—"),
                    "n_hits": int(cur_row.get("n_hits", 0) or 0),
                    "hits_per_min": round(hpm, 1),
                    "mean_int_g": round(mi, 2),
                    "max_int_g": round(float(cur_row.get("max_int_g", 0) or 0), 2),
                    "load_index": round(hpm * mi, 2),
                }
                rows.append(cur)

            if prev_row:
                hpm = float(prev_row.get("hits_per_min", 0) or 0)
                mi = float(prev_row.get("mean_int_g", 0) or 0)
                prev = {
                    "label": "Anterior",
                    "filename": prev_row.get("filename", "—"),
                    "n_hits": int(prev_row.get("n_hits", 0) or 0),
                    "hits_per_min": round(hpm, 1),
                    "mean_int_g": round(mi, 2),
                    "max_int_g": round(float(prev_row.get("max_int_g", 0) or 0), 2),
                    "load_index": round(hpm * mi, 2),
                }
                rows.append(prev)

            fig = go.Figure()

            if cur and prev:
                x = ["Seleccionada", "Anterior"]
                fig = _compare_bar_fig(
                    "IMU: carga externa (seleccionada vs anterior)",
                    x,
                    series=[
                        {"name": "Golpes/min", "y": [cur["hits_per_min"], prev["hits_per_min"]]},
                        {"name": "Intensidad media (g)", "y": [cur["mean_int_g"], prev["mean_int_g"]]},
                        {"name": "Índice carga", "y": [cur["load_index"], prev["load_index"]]},
                    ],
                    y_title="Valor",
                )
                badge = _load_badge(cur["load_index"], prev["load_index"], label="Carga externa")
            else:
                apply_chart_style(fig, title="IMU: faltan datos para comparar", x_title="Sesión", y_title="Valor", height=380)
                badge = _badge("No hay métricas IMU guardadas en ambas sesiones.", "neutral")

            return rows, fig, badge

        # ---------- EMG (sesión vs anterior) ----------
        @app.callback(
            Output("cmp-emg-sess-table", "data"),
            Output("cmp-emg-sess-bars", "figure"),
            Output("cmp-emg-sess-badge", "children"),
            Input("cmp-session-ids", "data"),
            State("cmp-user", "value"),
            prevent_initial_call=False,
        )
        def emg_session_compare(store, user_id):
            uid = _safe_int(user_id)
            if not uid or not store:
                return [], go.Figure(), None

            cur_id = _safe_int(store.get("cur"))
            prev_id = _safe_int(store.get("prev"))
            if not cur_id:
                return [], go.Figure(), None

            if not _has_sensor(db, uid, "EMG"):
                return [], go.Figure(), _badge("EMG no asignado a este deportista.", "neutral")

            cur_row = _latest_by_session(db, "emg", cur_id)
            prev_row = _latest_by_session(db, "emg", prev_id) if prev_id else None

            rows = []
            cur = prev = None

            if cur_row:
                cur = {
                    "label": "Seleccionada",
                    "filename": cur_row.get("filename", "—"),
                    "rms": round(float(cur_row.get("rms", 0) or 0), 3),
                    "peak": round(float(cur_row.get("peak", 0) or 0), 3),
                    "fatigue": round(float(cur_row.get("fatigue", 0) or 0), 1),
                }
                rows.append(cur)

            if prev_row:
                prev = {
                    "label": "Anterior",
                    "filename": prev_row.get("filename", "—"),
                    "rms": round(float(prev_row.get("rms", 0) or 0), 3),
                    "peak": round(float(prev_row.get("peak", 0) or 0), 3),
                    "fatigue": round(float(prev_row.get("fatigue", 0) or 0), 1),
                }
                rows.append(prev)

            fig = go.Figure()

            if cur and prev:
                x = ["Seleccionada", "Anterior"]
                fig = _compare_bar_fig(
                    "EMG: activación y fatiga (seleccionada vs anterior)",
                    x,
                    series=[
                        {"name": "RMS", "y": [cur["rms"], prev["rms"]]},
                        {"name": "Fatiga (%)", "y": [cur["fatigue"], prev["fatigue"]]},
                    ],
                    y_title="Valor",
                )
                badge = _fatigue_badge(cur["fatigue"], prev["fatigue"])
            else:
                apply_chart_style(fig, title="EMG: faltan datos para comparar", x_title="Sesión", y_title="Valor", height=380)
                badge = _badge("No hay métricas EMG guardadas en ambas sesiones.", "neutral")

            return rows, fig, badge

        # ---------- RESP (sesión vs anterior) ----------
        @app.callback(
            Output("cmp-resp-sess-table", "data"),
            Output("cmp-resp-sess-bars", "figure"),
            Output("cmp-resp-sess-badge", "children"),
            Input("cmp-session-ids", "data"),
            State("cmp-user", "value"),
            prevent_initial_call=False,
        )
        def resp_session_compare(store, user_id):
            uid = _safe_int(user_id)
            if not uid or not store:
                return [], go.Figure(), None

            cur_id = _safe_int(store.get("cur"))
            prev_id = _safe_int(store.get("prev"))
            if not cur_id:
                return [], go.Figure(), None

            if not _has_sensor(db, uid, "RESP_BELT"):
                return [], go.Figure(), _badge("Respiración no asignada a este deportista.", "neutral")

            cur_row = _latest_by_session(db, "resp", cur_id)
            prev_row = _latest_by_session(db, "resp", prev_id) if prev_id else None

            rows = []
            cur = prev = None

            if cur_row:
                br = float(cur_row.get("br_min", 0) or 0)
                cur = {
                    "label": "Seleccionada",
                    "filename": cur_row.get("filename", "—"),
                    "n_breaths": int(cur_row.get("n_breaths", 0) or 0),
                    "br_min": round(br, 1),
                    "mean_period": round(float(cur_row.get("mean_period", 0) or 0), 2),
                    "stress_index": round(br, 1),  # proxy simple
                }
                rows.append(cur)

            if prev_row:
                br = float(prev_row.get("br_min", 0) or 0)
                prev = {
                    "label": "Anterior",
                    "filename": prev_row.get("filename", "—"),
                    "n_breaths": int(prev_row.get("n_breaths", 0) or 0),
                    "br_min": round(br, 1),
                    "mean_period": round(float(prev_row.get("mean_period", 0) or 0), 2),
                    "stress_index": round(br, 1),
                }
                rows.append(prev)

            fig = go.Figure()

            if cur and prev:
                x = ["Seleccionada", "Anterior"]
                fig = _compare_bar_fig(
                    "Respiración: seleccionada vs anterior",
                    x,
                    series=[
                        {"name": "Resp/min", "y": [cur["br_min"], prev["br_min"]]},
                        {"name": "Periodo medio (s)", "y": [cur["mean_period"], prev["mean_period"]]},
                    ],
                    y_title="Valor",
                )
                badge = _resp_badge(cur["br_min"], prev["br_min"])
            else:
                apply_chart_style(fig, title="Respiración: faltan datos para comparar", x_title="Sesión", y_title="Valor", height=380)
                badge = _badge("No hay métricas de respiración guardadas en ambas sesiones.", "neutral")

            return rows, fig, badge

        # ---------- Resumen + recomendaciones en UI ----------
        @app.callback(
            Output("cmp-overall", "children"),
            Output("cmp-recs", "children"),
            Input("cmp-session-ids", "data"),
            State("cmp-user", "value"),
            prevent_initial_call=False,
        )
        def session_overall_ui(store, user_id):
            uid = _safe_int(user_id)
            if not uid or not store or not store.get("cur"):
                return "", []

            cur_id = _safe_int(store.get("cur"))
            prev_id = _safe_int(store.get("prev"))

            # Re-armamos badges con la misma lógica
            ecg_b = imu_b = emg_b = resp_b = None

            if _has_sensor(db, uid, "ECG"):
                ecg_cur = _ecg_row_for_session(db, uid, cur_id, "Seleccionada")
                ecg_prev = _ecg_row_for_session(db, uid, prev_id, "Anterior") if prev_id else None
                if ecg_cur and ecg_prev:
                    ecg_b = _ecg_recovery_badge(ecg_cur, ecg_prev)

            if _has_sensor(db, uid, "IMU"):
                r = _latest_by_session(db, "imu", cur_id)
                p = _latest_by_session(db, "imu", prev_id) if prev_id else None
                if r and p:
                    hpm_r = float(r.get("hits_per_min", 0) or 0)
                    mi_r = float(r.get("mean_int_g", 0) or 0)
                    hpm_p = float(p.get("hits_per_min", 0) or 0)
                    mi_p = float(p.get("mean_int_g", 0) or 0)
                    imu_b = _load_badge(hpm_r * mi_r, hpm_p * mi_p, label="Carga externa")

            if _has_sensor(db, uid, "EMG"):
                r = _latest_by_session(db, "emg", cur_id)
                p = _latest_by_session(db, "emg", prev_id) if prev_id else None
                if r and p:
                    emg_b = _fatigue_badge(float(r.get("fatigue", 0) or 0), float(p.get("fatigue", 0) or 0))

            if _has_sensor(db, uid, "RESP_BELT"):
                r = _latest_by_session(db, "resp", cur_id)
                p = _latest_by_session(db, "resp", prev_id) if prev_id else None
                if r and p:
                    resp_b = _resp_badge(float(r.get("br_min", 0) or 0), float(p.get("br_min", 0) or 0))

            overall = _overall_summary(ecg_b, imu_b, emg_b, resp_b)
            recs = _recommendations(ecg_b, imu_b, emg_b, resp_b)

            return overall, [html.Li(r) for r in recs]

        # ---------- Reporte PDF con imágenes ----------
        @app.callback(
            Output("cmp-report-dl", "data"),
            Output("cmp-report-msg", "children"),
            Input("btn-cmp-report", "n_clicks"),
            State("cmp-session-ids", "data"),
            State("cmp-user", "value"),
            prevent_initial_call=True,
        )
        def download_report(n, store, user_id):
            if not n:
                raise PreventUpdate

            if not _REPORTLAB_OK:
                # No tronamos la app: devolvemos un txt con instrucciones
                txt = (
                    "PDF deshabilitado porque falta reportlab.\n\n"
                    "Instala en tu entorno virtual:\n"
                    "  python -m pip install reportlab\n\n"
                    f"Detalle: {_REPORTLAB_ERR}\n"
                )
                return dcc.send_bytes(lambda b: b.write(txt.encode("utf-8")), "install_reportlab.txt"), "Instala reportlab para generar PDF."

            uid = _safe_int(user_id)
            if not uid or not store or not store.get("cur"):
                return dash.no_update, "Selecciona una sesión primero."

            cur_id = _safe_int(store.get("cur"))
            prev_id = _safe_int(store.get("prev"))

            athlete = None
            try:
                athlete = db.get_user_by_id(int(uid))
            except Exception:
                athlete = None

            # Recalcular datos como en pantalla
            ecg_cur = ecg_prev = None
            imu_cur = imu_prev = None
            emg_cur = emg_prev = None
            resp_cur = resp_prev = None

            ecg_badge = imu_badge = emg_badge = resp_badge = None

            if _has_sensor(db, uid, "ECG"):
                ecg_cur = _ecg_row_for_session(db, uid, cur_id, "Seleccionada")
                ecg_prev = _ecg_row_for_session(db, uid, prev_id, "Anterior") if prev_id else None
                if ecg_cur and ecg_prev:
                    ecg_badge = _ecg_recovery_badge(ecg_cur, ecg_prev)

            if _has_sensor(db, uid, "IMU"):
                r = _latest_by_session(db, "imu", cur_id)
                p = _latest_by_session(db, "imu", prev_id) if prev_id else None
                if r:
                    hpm = float(r.get("hits_per_min", 0) or 0)
                    mi = float(r.get("mean_int_g", 0) or 0)
                    imu_cur = {
                        "hits_per_min": hpm,
                        "mean_int_g": mi,
                        "load_index": hpm * mi,
                        "n_hits": int(r.get("n_hits", 0) or 0),
                        "max_int_g": float(r.get("max_int_g", 0) or 0),
                        "filename": r.get("filename", "—"),
                    }
                if p:
                    hpm = float(p.get("hits_per_min", 0) or 0)
                    mi = float(p.get("mean_int_g", 0) or 0)
                    imu_prev = {
                        "hits_per_min": hpm,
                        "mean_int_g": mi,
                        "load_index": hpm * mi,
                        "n_hits": int(p.get("n_hits", 0) or 0),
                        "max_int_g": float(p.get("max_int_g", 0) or 0),
                        "filename": p.get("filename", "—"),
                    }
                if imu_cur and imu_prev:
                    imu_badge = _load_badge(imu_cur["load_index"], imu_prev["load_index"], label="Carga externa")

            if _has_sensor(db, uid, "EMG"):
                r = _latest_by_session(db, "emg", cur_id)
                p = _latest_by_session(db, "emg", prev_id) if prev_id else None
                if r:
                    emg_cur = {
                        "rms": float(r.get("rms", 0) or 0),
                        "peak": float(r.get("peak", 0) or 0),
                        "fatigue": float(r.get("fatigue", 0) or 0),
                        "filename": r.get("filename", "—"),
                    }
                if p:
                    emg_prev = {
                        "rms": float(p.get("rms", 0) or 0),
                        "peak": float(p.get("peak", 0) or 0),
                        "fatigue": float(p.get("fatigue", 0) or 0),
                        "filename": p.get("filename", "—"),
                    }
                if emg_cur and emg_prev:
                    emg_badge = _fatigue_badge(emg_cur["fatigue"], emg_prev["fatigue"])

            if _has_sensor(db, uid, "RESP_BELT"):
                r = _latest_by_session(db, "resp", cur_id)
                p = _latest_by_session(db, "resp", prev_id) if prev_id else None
                if r:
                    br = float(r.get("br_min", 0) or 0)
                    resp_cur = {
                        "br_min": br,
                        "mean_period": float(r.get("mean_period", 0) or 0),
                        "n_breaths": int(r.get("n_breaths", 0) or 0),
                        "filename": r.get("filename", "—"),
                    }
                if p:
                    br = float(p.get("br_min", 0) or 0)
                    resp_prev = {
                        "br_min": br,
                        "mean_period": float(p.get("mean_period", 0) or 0),
                        "n_breaths": int(p.get("n_breaths", 0) or 0),
                        "filename": p.get("filename", "—"),
                    }
                if resp_cur and resp_prev:
                    resp_badge = _resp_badge(resp_cur["br_min"], resp_prev["br_min"])

            overall = _overall_summary(ecg_badge, imu_badge, emg_badge, resp_badge)
            recs = _recommendations(ecg_badge, imu_badge, emg_badge, resp_badge)

            # ---- Figuras para PDF
            figs = []
            if ecg_cur and ecg_prev:
                fig = go.Figure()
                x = ["Seleccionada", "Anterior"]
                fig.add_trace(go.Bar(x=x, y=[ecg_cur["bpm"], ecg_prev["bpm"]], name="BPM"))
                fig.add_trace(go.Bar(x=x, y=[ecg_cur["sdnn_ms"], ecg_prev["sdnn_ms"]], name="SDNN (ms)"))
                fig.add_trace(go.Bar(x=x, y=[ecg_cur["rmssd_ms"], ecg_prev["rmssd_ms"]], name="RMSSD (ms)"))
                fig.update_layout(barmode="group", template="plotly_dark", height=360, title="ECG/HRV")
                figs.append(("ECG/HRV", fig))

            if imu_cur and imu_prev:
                fig = go.Figure()
                x = ["Seleccionada", "Anterior"]
                fig.add_trace(go.Bar(x=x, y=[imu_cur["hits_per_min"], imu_prev["hits_per_min"]], name="Golpes/min"))
                fig.add_trace(go.Bar(x=x, y=[imu_cur["mean_int_g"], imu_prev["mean_int_g"]], name="Int media (g)"))
                fig.add_trace(go.Bar(x=x, y=[imu_cur["load_index"], imu_prev["load_index"]], name="Índice carga"))
                fig.update_layout(barmode="group", template="plotly_dark", height=360, title="IMU")
                figs.append(("IMU", fig))

            if emg_cur and emg_prev:
                fig = go.Figure()
                x = ["Seleccionada", "Anterior"]
                fig.add_trace(go.Bar(x=x, y=[emg_cur["rms"], emg_prev["rms"]], name="RMS"))
                fig.add_trace(go.Bar(x=x, y=[emg_cur["fatigue"], emg_prev["fatigue"]], name="Fatiga (%)"))
                fig.update_layout(barmode="group", template="plotly_dark", height=360, title="EMG")
                figs.append(("EMG", fig))

            if resp_cur and resp_prev:
                fig = go.Figure()
                x = ["Seleccionada", "Anterior"]
                fig.add_trace(go.Bar(x=x, y=[resp_cur["br_min"], resp_prev["br_min"]], name="Resp/min"))
                fig.add_trace(go.Bar(x=x, y=[resp_cur["mean_period"], resp_prev["mean_period"]], name="Periodo (s)"))
                fig.update_layout(barmode="group", template="plotly_dark", height=360, title="Respiración")
                figs.append(("Respiración", fig))

            # ---- PDF
            buf = io.BytesIO()
            c = canvas.Canvas(buf, pagesize=A4)
            width, height = A4
            x0 = 2 * cm
            y = height - 2 * cm
            page_num = 1
            # header line
            c.setStrokeColor(colors.HexColor("#E2E8F0"))
            c.setLineWidth(1)
            c.line(x0, height - 2.2 * cm, width - 2 * cm, height - 2.2 * cm)

            def page_break(ypos):
                nonlocal page_num
                if ypos < 2 * cm:
                    # footer
                    c.setFillColor(colors.HexColor("#64748b"))
                    c.setFont("Helvetica", 8)
                    c.drawString(x0, 1.3 * cm, "PowerSync")
                    c.drawRightString(width - 2 * cm, 1.3 * cm, f"Página {page_num}")
                    c.setStrokeColor(colors.HexColor("#E2E8F0"))
                    c.setLineWidth(1)
                    c.line(x0, 1.8 * cm, width - 2 * cm, 1.8 * cm)

                    c.showPage()
                    page_num += 1
                    # header line on new page
                    c.setStrokeColor(colors.HexColor("#E2E8F0"))
                    c.setLineWidth(1)
                    c.line(x0, height - 2.2 * cm, width - 2 * cm, height - 2.2 * cm)
                    return height - 2 * cm
                return ypos

            def draw_paragraph(text, ypos, font="Helvetica", size=10, leading=14, color=colors.HexColor("#0E1522")):
                # Siempre fijamos color para evitar heredar colores claros del table/graficas
                c.setFillColor(color)
                c.setFont(font, size)
                wrapped = simpleSplit(text, font, size, width - 4 * cm)
                for line in wrapped:
                    c.drawString(x0, ypos, line)
                    ypos -= leading
                    ypos = page_break(ypos)
                    c.setFillColor(color)
                    c.setFont(font, size)
                return ypos

            def draw_table(ypos, headers, rows, col_widths):
                row_h = 18
                table_w = sum(col_widths)
                ypos = page_break(ypos)

                # Header
                header_fill = colors.HexColor("#0E1522")
                header_txt = colors.white
                border = colors.HexColor("#D6DEE8")
                zebra_a = colors.HexColor("#F5F7FB")
                zebra_b = colors.white
                body_txt = colors.HexColor("#0E1522")
                muted_txt = colors.HexColor("#334155")

                c.setFillColor(header_fill)
                c.roundRect(x0, ypos - row_h, table_w, row_h, 6, fill=1, stroke=0)
                c.setFillColor(header_txt)
                c.setFont("Helvetica-Bold", 9)

                xx = x0
                for h, w in zip(headers, col_widths):
                    c.drawString(xx + 6, ypos - 12, str(h))
                    xx += w

                c.setStrokeColor(border)
                c.setLineWidth(1)
                c.roundRect(x0, ypos - row_h, table_w, row_h, 6, fill=0, stroke=1)
                ypos -= row_h

                c.setFont("Helvetica", 9)
                for i, r in enumerate(rows):
                    if ypos - row_h < 2 * cm:
                        # page break with header repeated
                        ypos = page_break(ypos - row_h)
                        c.setFillColor(header_fill)
                        c.roundRect(x0, ypos - row_h, table_w, row_h, 6, fill=1, stroke=0)
                        c.setFillColor(header_txt)
                        c.setFont("Helvetica-Bold", 9)
                        xx = x0
                        for h, w in zip(headers, col_widths):
                            c.drawString(xx + 6, ypos - 12, str(h))
                            xx += w
                        c.setStrokeColor(border)
                        c.setLineWidth(1)
                        c.roundRect(x0, ypos - row_h, table_w, row_h, 6, fill=0, stroke=1)
                        ypos -= row_h
                        c.setFont("Helvetica", 9)

                    c.setFillColor(zebra_a if i % 2 == 0 else zebra_b)
                    c.rect(x0, ypos - row_h, table_w, row_h, fill=1, stroke=0)

                    c.setFillColor(body_txt)
                    xx = x0
                    for cell, w in zip(r, col_widths):
                        txt = str(cell)
                        if len(txt) > 34:
                            txt = txt[:31] + "..."
                        c.drawString(xx + 6, ypos - 12, txt)
                        xx += w

                    c.setStrokeColor(border)
                    c.setLineWidth(0.8)
                    c.line(x0, ypos - row_h, x0 + table_w, ypos - row_h)
                    ypos -= row_h

                return ypos - 10

            def fig_to_png_bytes(fig):
                try:
                    return fig.to_image(format="png", scale=2), None
                except Exception as e:
                    return None, str(e)

            def draw_plot_image(ypos, title, png_bytes, max_h_cm=8.8):
                ypos = page_break(ypos)
                # Card header
                c.setFillColor(colors.HexColor("#0E1522"))
                c.setFont("Helvetica-Bold", 11)
                c.drawString(x0, ypos, title)
                ypos -= 10

                img = ImageReader(io.BytesIO(png_bytes))
                img_w, img_h = img.getSize()

                max_w = width - 4 * cm
                max_h = max_h_cm * cm
                scale = min(max_w / img_w, max_h / img_h)
                draw_w = img_w * scale
                draw_h = img_h * scale

                if ypos - draw_h < 2 * cm:
                    ypos = page_break(ypos - draw_h)
                    c.setFillColor(colors.HexColor("#0E1522"))
                    c.setFont("Helvetica-Bold", 11)
                    c.drawString(x0, ypos, title)
                    ypos -= 10

                # light frame
                c.setStrokeColor(colors.HexColor("#D6DEE8"))
                c.setLineWidth(1)
                c.roundRect(x0, ypos - draw_h - 6, draw_w, draw_h + 6, 8, fill=0, stroke=1)
                c.drawImage(img, x0, ypos - draw_h, width=draw_w, height=draw_h, mask="auto")
                ypos -= (draw_h + 18)
                return ypos

            name = (athlete or {}).get("name", "Deportista")
            sport = (athlete or {}).get("sport", "—")

            y = draw_paragraph("PowerSync — Informe de sesión", y, font="Helvetica-Bold", size=18, leading=20, color=colors.HexColor("#0E1522"))
            y -= 6
            y = draw_paragraph(f"Deportista: {name}  |  Deporte: {sport}", y, color=colors.HexColor("#334155"))
            y = draw_paragraph(f"Generado: {datetime.utcnow().isoformat()[:19].replace('T',' ')} UTC", y, color=colors.HexColor("#334155"))
            y = draw_paragraph(f"Sesión seleccionada: #{cur_id}", y, color=colors.HexColor("#334155"))
            y = draw_paragraph(f"Sesión anterior: #{prev_id if prev_id else '—'}", y, color=colors.HexColor("#334155"))
            y -= 10

            y = draw_paragraph("Resumen ejecutivo", y, font="Helvetica-Bold", size=13, leading=18, color=colors.HexColor("#0E1522"))
            y = draw_paragraph(overall, y, font="Helvetica", size=11, leading=15, color=colors.HexColor("#0E1522"))

            y -= 8
            y = draw_paragraph("Tabla comparativa (Seleccionada vs Anterior)", y, font="Helvetica-Bold", size=13, leading=18, color=colors.HexColor("#0E1522"))

            headers = ["Métrica", "Seleccionada", "Anterior", "Δ", "%"]
            col_widths = [6.0 * cm, 3.0 * cm, 3.0 * cm, 2.2 * cm, 2.0 * cm]

            def _row_metric(label, cur, prev, unit="", decimals=2):
                if cur is None or prev is None:
                    return [label, "—", "—", "—", "—"]
                d, pct = _delta_and_pct(cur, prev)
                cur_s = f"{float(cur):.{decimals}f}{unit}"
                prev_s = f"{float(prev):.{decimals}f}{unit}"
                d_s = f"{d:+.{decimals}f}{unit}" if d is not None else "—"
                pct_s = _fmt_pct(pct)
                return [label, cur_s, prev_s, d_s, pct_s]

            table_rows = []

            if ecg_cur and ecg_prev:
                table_rows += [
                    _row_metric("ECG BPM", ecg_cur.get("bpm"), ecg_prev.get("bpm"), unit="", decimals=0),
                    _row_metric("ECG SDNN", ecg_cur.get("sdnn_ms"), ecg_prev.get("sdnn_ms"), unit=" ms", decimals=0),
                    _row_metric("ECG RMSSD", ecg_cur.get("rmssd_ms"), ecg_prev.get("rmssd_ms"), unit=" ms", decimals=0),
                ]
            if imu_cur and imu_prev:
                table_rows += [
                    _row_metric("IMU golpes/min", imu_cur["hits_per_min"], imu_prev["hits_per_min"], unit="", decimals=1),
                    _row_metric("IMU int media", imu_cur["mean_int_g"], imu_prev["mean_int_g"], unit=" g", decimals=2),
                    _row_metric("IMU carga externa", imu_cur["load_index"], imu_prev["load_index"], unit="", decimals=2),
                ]
            if emg_cur and emg_prev:
                table_rows += [
                    _row_metric("EMG RMS", emg_cur["rms"], emg_prev["rms"], unit="", decimals=3),
                    _row_metric("EMG fatiga", emg_cur["fatigue"], emg_prev["fatigue"], unit=" %", decimals=1),
                ]
            if resp_cur and resp_prev:
                table_rows += [
                    _row_metric("Resp/min", resp_cur["br_min"], resp_prev["br_min"], unit="", decimals=1),
                    _row_metric("Periodo resp", resp_cur["mean_period"], resp_prev["mean_period"], unit=" s", decimals=2),
                ]

            if not table_rows:
                table_rows = [["—", "—", "—", "—", "—"]]

            y = draw_table(y, headers, table_rows, col_widths)

            y = draw_paragraph("Gráficas comparativas", y, font="Helvetica-Bold", size=13, leading=18, color=colors.HexColor("#0E1522"))
            any_img_error = False
            for title, fig in figs:
                png, err = fig_to_png_bytes(fig)
                if png:
                    y = draw_plot_image(y, title, png, max_h_cm=8.8)
                else:
                    any_img_error = True
                    y = draw_paragraph(f"- {title}: no se pudo renderizar imagen ({err}).", y, size=9, leading=12)

            if any_img_error:
                y = draw_paragraph(
                    "Tip: para habilitar imágenes instala kaleido: python -m pip install -U kaleido",
                    y, size=9, leading=12
                )

            y = draw_paragraph("Recomendaciones", y, font="Helvetica-Bold", size=13, leading=18, color=colors.HexColor("#0E1522"))
            for r in recs:
                y = draw_paragraph(f" - {r}", y)

            y = draw_paragraph("Nota", y, font="Helvetica-Bold", size=13, leading=18, color=colors.HexColor("#0E1522"))
            y = draw_paragraph("Interpretación heurística para entrenamiento (no diagnóstico).", y, size=9, leading=12)

                        # footer última página
            c.setFillColor(colors.HexColor("#64748b"))
            c.setFont("Helvetica", 8)
            c.drawString(x0, 1.3 * cm, "PowerSync")
            c.drawRightString(width - 2 * cm, 1.3 * cm, f"Página {page_num}")
            c.setStrokeColor(colors.HexColor("#E2E8F0"))
            c.setLineWidth(1)
            c.line(x0, 1.8 * cm, width - 2 * cm, 1.8 * cm)

            c.save()
            pdf_bytes = buf.getvalue()
            buf.close()

            filename = f"PowerSync_Informe_Sesion_{cur_id}.pdf"
            return dcc.send_bytes(lambda b: b.write(pdf_bytes), filename), ""

        # ---------- ECG clásico: cargar archivos por deportista ----------
        @app.callback(
            Output("cmp-ecg-table", "data"),
            Input("cmp-user", "value"),
            prevent_initial_call=False,
        )
        def load_ecg_files_table(user_id):
            uid = _safe_int(user_id)
            if not uid:
                return []

            files = db.list_ecg_files(int(uid)) or []
            rows = []

            for f in files:
                fname = f.get("filename")
                if not fname:
                    continue
                path = os.path.join("data", "ecg", fname)
                if not os.path.exists(path):
                    continue

                try:
                    fs0 = int(f.get("fs") or 250)
                except Exception:
                    fs0 = 250

                try:
                    t, x, fs = read_ecg_csv(path, fs_default=fs0)
                except Exception:
                    continue

                if x is None or len(x) < 5:
                    continue

                try:
                    xs = smooth(x, win_ms=40, fs=fs)
                    peaks = detect_r_peaks(xs, fs, sens=0.6)
                    bpm, sdnn, rmssd = ecg_metrics_from_peaks(peaks, fs)
                except Exception:
                    bpm, sdnn, rmssd = 0.0, 0.0, 0.0
                    peaks = np.array([], dtype=int)

                duration_s = float(t[-1] - t[0]) if len(t) > 1 else 0.0
                n_beats = int(len(peaks))

                rows.append(
                    {
                        "id": f.get("id"),
                        "filename": fname,
                        "duration_s": round(duration_s, 1),
                        "n_beats": n_beats,
                        "bpm": int(round(bpm)) if bpm > 0 else 0,
                        "sdnn_ms": int(round(sdnn)) if sdnn > 0 else 0,
                        "rmssd_ms": int(round(rmssd)) if rmssd > 0 else 0,
                    }
                )

            return rows

        # ---------- ECG clásico: gráfico de barras ----------
        @app.callback(
            Output("cmp-ecg-bars", "figure"),
            Input("cmp-ecg-table", "data"),
            Input("cmp-ecg-table", "selected_rows"),
            prevent_initial_call=False,
        )
        def ecg_compare_bars(data, selected_rows):
            if not data:
                return go.Figure()

            if not selected_rows:
                selected_rows = list(range(min(4, len(data))))
            selected_rows = selected_rows[:4]

            sel = [data[i] for i in selected_rows if 0 <= i < len(data)]
            if not sel:
                return go.Figure()

            labels = [row.get("filename", "sesión") for row in sel]
            bpm = [row.get("bpm", 0) for row in sel]
            sdnn = [row.get("sdnn_ms", 0) for row in sel]
            rmssd = [row.get("rmssd_ms", 0) for row in sel]

            fig = go.Figure()
            fig.add_trace(go.Bar(x=labels, y=bpm, name="BPM"))
            fig.add_trace(go.Bar(x=labels, y=sdnn, name="SDNN (ms)"))
            fig.add_trace(go.Bar(x=labels, y=rmssd, name="RMSSD (ms)"))

            fig.update_layout(barmode="group")
            apply_chart_style(fig, title="Comparativa HRV entre archivos", x_title="Archivo", y_title="Valor", height=420)
            return fig

        # ---------- Helper multi-upload ----------
        def _decode_multiple_uploads(contents, filenames, subfolder):
            if not contents:
                return []

            if not isinstance(contents, list):
                contents = [contents]
            if not isinstance(filenames, list):
                filenames = [filenames]

            n = min(len(contents), len(filenames)) if filenames else len(contents)
            if n <= 0:
                return []

            dirpath = os.path.join("data", subfolder)
            os.makedirs(dirpath, exist_ok=True)

            result = []
            for idx in range(n):
                c = contents[idx]
                fn = filenames[idx] if idx < len(filenames) else f"session_{idx}.csv"
                if not c:
                    continue

                try:
                    data = _b64_to_bytes(c)
                except Exception:
                    continue

                safe_label = _sanitize_filename(fn or f"session_{idx}.csv")
                try:
                    saved_name = _save_unique(dirpath, safe_label, data, prefix="cmp_")
                except Exception:
                    continue

                path = os.path.join(dirpath, saved_name)
                session_name = safe_label
                result.append((session_name, path))

            return result

        # ---------- IMU: comparar (multi-upload) ----------
        @app.callback(
            Output("cmp-imu-table", "data"),
            Output("cmp-imu-bars", "figure"),
            Input("cmp-imu-upload", "contents"),
            State("cmp-imu-upload", "filename"),
            prevent_initial_call=True,
        )
        def compare_imu(contents, filenames):
            sessions = _decode_multiple_uploads(contents, filenames, subfolder="imu_compare")
            if not sessions:
                return [], go.Figure()

            rows = []
            labels = []
            hits_per_min_list = []
            mean_int_list = []

            for session_name, path in sessions:
                try:
                    t, mag, fs = read_imu_csv(path)
                except Exception:
                    continue
                if mag is None or len(mag) == 0:
                    continue

                n_hits, hits_per_min, mean_int, max_int, _ = imu_metrics_from_mag(mag, t, fs)

                row = {
                    "session": session_name,
                    "n_hits": int(n_hits),
                    "hits_per_min": round(float(hits_per_min), 1),
                    "mean_int_g": round(float(mean_int), 2),
                    "max_int_g": round(float(max_int), 2),
                }
                rows.append(row)

                labels.append(session_name)
                hits_per_min_list.append(row["hits_per_min"])
                mean_int_list.append(row["mean_int_g"])

            if not rows:
                return [], go.Figure()

            fig = go.Figure()
            fig.add_trace(go.Bar(x=labels, y=hits_per_min_list, name="Golpes/min"))
            fig.add_trace(go.Bar(x=labels, y=mean_int_list, name="Intensidad media (g)"))
            fig.update_layout(barmode="group")
            apply_chart_style(fig, title="Comparativa IMU (golpes/min e intensidad)", x_title="Archivo", y_title="Valor", height=420)

            return rows, fig

        # ---------- EMG: comparar (multi-upload) ----------
        @app.callback(
            Output("cmp-emg-table", "data"),
            Output("cmp-emg-bars", "figure"),
            Input("cmp-emg-upload", "contents"),
            State("cmp-emg-upload", "filename"),
            prevent_initial_call=True,
        )
        def compare_emg(contents, filenames):
            sessions = _decode_multiple_uploads(contents, filenames, subfolder="emg_compare")
            if not sessions:
                return [], go.Figure()

            rows = []
            labels = []
            rms_list = []
            fatigue_list = []

            for session_name, path in sessions:
                try:
                    t, x, fs = read_emg_csv(path)
                except Exception:
                    continue
                if x is None or len(x) == 0:
                    continue

                rms, peak, fatigue = emg_metrics(x, fs)

                row = {
                    "session": session_name,
                    "rms": round(float(rms), 3),
                    "peak": round(float(peak), 3),
                    "fatigue": round(float(fatigue), 1),
                }
                rows.append(row)

                labels.append(session_name)
                rms_list.append(row["rms"])
                fatigue_list.append(row["fatigue"])

            if not rows:
                return [], go.Figure()

            fig = go.Figure()
            fig.add_trace(go.Bar(x=labels, y=rms_list, name="RMS global"))
            fig.add_trace(go.Bar(x=labels, y=fatigue_list, name="Fatiga (%)"))
            fig.update_layout(barmode="group")
            apply_chart_style(fig, title="Comparativa EMG (RMS y fatiga)", x_title="Archivo", y_title="Valor", height=420)

            return rows, fig

        # ---------- RESP: comparar (multi-upload) ----------
        @app.callback(
            Output("cmp-resp-table", "data"),
            Output("cmp-resp-bars", "figure"),
            Input("cmp-resp-upload", "contents"),
            State("cmp-resp-upload", "filename"),
            prevent_initial_call=True,
        )
        def compare_resp(contents, filenames):
            sessions = _decode_multiple_uploads(contents, filenames, subfolder="resp_compare")
            if not sessions:
                return [], go.Figure()

            rows = []
            labels = []
            br_min_list = []

            for session_name, path in sessions:
                try:
                    t, x, fs = read_resp_csv(path)
                except Exception:
                    continue
                if x is None or len(x) == 0:
                    continue

                n_breaths, br_min, mean_period, _ = resp_metrics(t, x, fs, sens=0.6)

                row = {
                    "session": session_name,
                    "n_breaths": int(n_breaths),
                    "br_min": round(float(br_min), 1),
                    "mean_period": round(float(mean_period), 2),
                }
                rows.append(row)

                labels.append(session_name)
                br_min_list.append(row["br_min"])

            if not rows:
                return [], go.Figure()

            fig = go.Figure()
            fig.add_trace(go.Bar(x=labels, y=br_min_list, name="Resp/min"))
            apply_chart_style(fig, title="Comparativa respiración (respiraciones/min)", x_title="Archivo", y_title="Respiraciones/min", height=420)

            return rows, fig
