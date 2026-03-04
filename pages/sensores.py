import dash
from dash import html
dash.register_page(__name__, path="/sensores")
try:
    from sensors import calibration_layout as calib  # opcional si tienes layout de calibración
    layout = calib
except Exception:
    layout = html.Div([html.H1("Sensores & calibración"), html.P("Emparejar/batería/test de señal…")])
