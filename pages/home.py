# pages/home.py
import dash
from dash import html, dcc

dash.register_page(__name__, path="/")  # Home en "/"

def tile(title, href, icon):
    return dcc.Link(
        html.Div([
            html.Img(src=f"/assets/icons/{icon}", className="tile-icon"),
            html.Div(title, className="tile-title")
        ], className="tile-card"),
        href=href, className="tile-link"
    )

layout = html.Div([
    html.H1("Inicio", className="page-title"),
    html.Div([
        tile("Sesión rápida", "/sesion", "session.svg"),
        tile("Cargar señales", "/senales", "signals.svg"),
        tile("Cuestionario bienestar", "/wellbeing", "wellbeing.svg"),
        tile("Histórico", "/historico", "history.svg"),
        tile("Comparar sesiones", "/comparar", "compare.svg"),
        tile("Sensores & calibración", "/sensores", "sensors.svg"),
        tile("Plan de peso", "/peso", "weight.svg"),
        tile("Nutrición", "/nutricion", "nutrition.svg"),
        tile("Equipo / Coach", "/equipo", "team.svg"),
        tile("QR & Perfil", "/perfil", "profile.svg"),
    ], className="grid"),
])
