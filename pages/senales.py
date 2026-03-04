import dash
from dash import html

import db
import sensors
from views.signals_view import SignalsView

dash.register_page(__name__, path="/senales", name="Señales")

# Instancia lazy para evitar problemas de import/ciclos
_view = None

def _get_view():
    global _view
    if _view is None:
        app = dash.get_app()
        _view = SignalsView(app, db, sensors)
    return _view

def layout():
    try:
        return _get_view().layout()
    except Exception as e:
        return html.Div([
            html.H3("Error cargando Señales"),
            html.Div(str(e), style={"opacity": 0.8}),
        ])
