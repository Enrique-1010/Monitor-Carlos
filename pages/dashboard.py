from dash import html
from flask import session
import db


def _to_str(v):
    if isinstance(v, (bytes, bytearray)):
        try:
            return v.decode("utf-8")
        except Exception:
            return v.decode("latin1", "ignore")
    return v


def _safe_str(value, default="-"):
    value = _to_str(value)
    if not value:
        return default
    return str(value)


def layout():
    """Vista de "QR & Perfil".

    - Si el usuario NO ha iniciado sesión: muestra el dashboard genérico.
    - Si el usuario es deportista: muestra un mini resumen de perfil + último wellness + última HRV.
    - Si el usuario es coach u otro rol: mantiene el dashboard genérico.
    """
    uid = session.get("user_id")
    role = _to_str(session.get("role")) or "no autenticado"

    # Sin sesión: dejamos el layout simple de bienvenida
    if not uid:
        return html.Div([
            html.H2("Dashboard"),
            html.P("Bienvenido a PowerSync. Usa el menú para comenzar: Usuarios, Sensores, ECG o Cuestionario."),
            html.Div(className="kpis", children=[
                html.Div(className="kpi", children=[
                    html.Div("Estado", className="kpi-label"),
                    html.Div("Invitado", className="kpi-value"),
                    html.Div(className="kpi-ecg-line")
                ]),
                html.Div(className="kpi", children=[
                    html.Div("Módulos", className="kpi-label"),
                    html.Div(
                        "Usuarios • Sensores • ECG • Cuestionario",
                        className="kpi-value",
                        style={"fontSize": "16px"}
                    ),
                    html.Div(className="kpi-ecg-line")
                ]),
            ])
        ])

    # Obtenemos datos del usuario logueado
    try:
        uid_int = int(uid)
    except (TypeError, ValueError):
        uid_int = None

    user = db.get_user_by_id(uid_int) if uid_int else None

    # Si algo falla, volvemos al dashboard simple
    if not user:
        return html.Div([
            html.H2("Dashboard"),
            html.P("No se pudieron cargar los datos de tu perfil. Intenta volver a iniciar sesión."),
        ])

    name = _safe_str(user.get("name"), "Sin nombre")
    sport = _safe_str(user.get("sport"), "Sin deporte definido")
    created_at = user.get("created_at") or ""
    created_pretty = created_at[:10] if created_at else "-"  # YYYY-MM-DD

    # Último cuestionario de bienestar
    last_wellness = "Sin registros"
    last_wellness_val = None
    try:
        qs = db.list_questionnaires(uid_int)
        if qs:
            q0 = qs[0]
            last_wellness_val = q0.get("wellness_score", None)
            ts = q0.get("ts") or ""
            ts_pretty = ts.replace("T", " ")[:16] if ts else ""
            if last_wellness_val is not None:
                last_wellness = f"{last_wellness_val:.0f} / 100 · {ts_pretty}"
            else:
                last_wellness = ts_pretty or "Sin registros"
    except Exception:
        last_wellness = "Sin registros"
        last_wellness_val = None

    # Últimas métricas de HRV (ECG)
    last_bpm = "Sin registros"
    last_hrv_detail = ""
    try:
        hrv = db.get_last_ecg_metrics(uid_int)
        if hrv:
            bpm = hrv.get("bpm", None)
            sdnn = hrv.get("sdnn", None)
            rmssd = hrv.get("rmssd", None)
            if bpm is not None:
                last_bpm = f"{bpm:.0f} bpm"
            if sdnn is not None or rmssd is not None:
                parts = []
                if sdnn is not None:
                    parts.append(f"SDNN {sdnn:.1f} ms")
                if rmssd is not None:
                    parts.append(f"RMSSD {rmssd:.1f} ms")
                last_hrv_detail = " · ".join(parts)
    except Exception:
        last_bpm = "Sin registros"
        last_hrv_detail = ""

    # Si el usuario es deportista, mostramos perfil + KPIs
    if role == "deportista":
        return html.Div([
            html.H2("Mi panel"),

            html.Div(className="kpis", children=[
                # Perfil
                html.Div(className="kpi", children=[
                    html.Div("Perfil", className="kpi-label"),
                    html.Div(name, className="kpi-value"),
                    html.Div(f"Deporte: {sport}", className="kpi-sub"),
                    html.Div(f"Desde: {created_pretty}", className="kpi-sub"),
                    html.Div(className="kpi-ecg-line")
                ]),

                # Bienestar
                html.Div(className="kpi", children=[
                    html.Div("Bienestar", className="kpi-label"),
                    html.Div(
                        _safe_str(
                            f"{last_wellness_val:.0f} / 100" if last_wellness_val is not None else "Sin datos"
                        ),
                        className="kpi-value"
                    ),
                    html.Div(last_wellness if isinstance(last_wellness, str) else "", className="kpi-sub"),
                    html.Div(className="kpi-ecg-line")
                ]),

                # HRV / ECG
                html.Div(className="kpi", children=[
                    html.Div("Cardio / HRV", className="kpi-label"),
                    html.Div(last_bpm, className="kpi-value"),
                    html.Div(
                        last_hrv_detail or "Sube un ECG para ver tus métricas.",
                        className="kpi-sub"
                    ),
                    html.Div(className="kpi-ecg-line")
                ]),
            ]),

            html.P(
                "Usa el menú de la izquierda para cargar nuevas sesiones de ECG o responder tu cuestionario diario.",
                style={"marginTop": "18px", "opacity": 0.85}
            )
        ])

    # Para coach u otros roles, mantenemos el dashboard clásico (pero con nombre)
    return html.Div([
        html.H2("Dashboard"),

        html.P(
            f"Bienvenido a PowerSync, {name}. Usa el menú para moverte por Usuarios, Sensores, ECG o Cuestionario."
        ),

        html.Div(className="kpis", children=[
            html.Div(className="kpi", children=[
                html.Div("Rol", className="kpi-label"),
                html.Div(str(role).capitalize(), className="kpi-value"),
                html.Div(className="kpi-ecg-line")
            ]),
            html.Div(className="kpi", children=[
                html.Div("Módulos", className="kpi-label"),
                html.Div(
                    "Usuarios • Sensores • ECG • Cuestionario",
                    className="kpi-value",
                    style={"fontSize": "16px"}
                ),
                html.Div(className="kpi-ecg-line")
            ]),
        ])
    ])
