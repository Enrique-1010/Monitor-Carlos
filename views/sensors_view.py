# views/sensors_view.py

from flask import session
from dash import html, dcc, Input, Output, State
from dash.exceptions import PreventUpdate


class SensorsView:
    """
    Vista 'Sensores & calibración'.

    - Para COACH / ADMIN:
        * Seleccionar deportista
        * Asignar sensores (ECG, IMU, EMG, RESP_BELT)
        * Ver tarjetas con:
            - descripción del sensor
            - métricas que desbloquea
            - pestañas de 'Cargar señales' donde se usa

    - Para DEPORTISTA:
        * Ver 'Mis sensores' con explicación breve y última métrica (si existe).
    """

    _callbacks_registered = False  # ✅ evita registro doble

    def __init__(self, app, db, sensors_module):
        self.app = app
        self.db = db
        self.S = sensors_module

        # ✅ registra callbacks solo una vez
        if not SensorsView._callbacks_registered:
            self._register_callbacks()
            SensorsView._callbacks_registered = True

    # ---------- Layout principal ----------

    def layout(self):
        if not session.get("user_id"):
            return html.Div("Inicia sesión para ver esta página.")

        role = str(session.get("role") or "no autenticado")

        if role in ("coach", "admin"):
            return self._layout_coach_admin(role)

        if role == "deportista":
            return self._layout_athlete()

        return html.Div("No tienes permisos para ver esta página.")

    # ---------- Helpers UI ----------

    def _build_sensor_cards(self, codes, user_id_for_last_metrics=None):
        """
        Construye cards de sensores usando clases CSS (sin estilos inline)
        para mantener consistencia visual a nivel app.
        """
        S = self.S
        db = self.db

        try:
            last_ecg = db.get_last_ecg_metrics(int(user_id_for_last_metrics)) if user_id_for_last_metrics else None
        except Exception:
            last_ecg = None

        cards = []
        for code in (codes or []):
            info = S.catalog().get(code, {})
            name = info.get("name", code)
            desc = S.description(code)
            signals = S.pretty_signals_for(code)
            metrics = S.metrics_for(code)

            if code == "ECG" and last_ecg:
                try:
                    last_metric = f"{float(last_ecg.get('bpm', 0) or 0):.0f} BPM"
                except Exception:
                    last_metric = "—"
            else:
                last_metric = "—"

            cards.append(
                html.Div(
                    className="card sensor-card",
                    children=[
                        html.Div(
                            className="sensor-card__row",
                            children=[
                                html.Div(name, className="sensor-card__title"),
                                html.Small("Última métrica: " + last_metric, className="sensor-card__last"),
                            ],
                        ),
                        html.Div(desc, className="sensor-card__desc"),
                        html.Div(
                            f"Señales en PowerSync: {signals}",
                            className="sensor-card__meta",
                        ),
                        html.Div(
                            f"Métricas estimadas: {', '.join(metrics) if metrics else '—'}",
                            className="sensor-card__meta",
                        ),
                    ],
                )
            )
        return cards

    def _hidden_placeholders_for_callbacks(self):
        """
        Evita errores si Dash valida callbacks y no encuentra IDs
        cuando el layout del deportista NO incluye componentes del coach/admin.
        """
        return html.Div(
            style={"display": "none"},
            children=[
                dcc.Dropdown(id="sel-user-sens"),
                dcc.Checklist(id="chk-sensors"),
                html.Button(id="btn-save-sens"),
                html.Div(id="sens-msg"),
                html.Div(id="sensor-info"),
            ],
        )

    # ---------- Layout COACH / ADMIN ----------

    def _layout_coach_admin(self, role: str):
        user_id = session.get("user_id")

        if role == "coach" and user_id:
            athletes = self.db.list_athletes_for_coach(int(user_id))
        else:  # admin
            athletes = [
                u for u in self.db.list_users()
                if (u.get("role", "deportista") == "deportista")
            ]

        options_users = [
            {"label": f"{u['name']} · {u.get('sport', '-')}", "value": u["id"]}
            for u in athletes
        ]

        checklist = dcc.Checklist(
            id="chk-sensors",
            options=self.S.labels_for_checklist(),
            value=[],
            inputStyle={"marginRight": "8px"},
            labelStyle={"display": "block", "marginBottom": "6px"},
        )

        return html.Div(
            className="sensors-page",
            children=[
                html.Div(
                    className="page-head",
                    children=[
                        html.H2("Sensores & asignación"),
                        html.Div(
                            "Como coach puedes asignar sensores a tus deportistas. "
                            "Cada sensor desbloquea métricas específicas en la vista de 'Cargar señales'.",
                            className="page-sub",
                        ),
                    ],
                ),
                html.Div(className="ecg-divider"),
                html.Div(
                    className="grid-2col",
                    children=[
                        html.Div(
                            className="card",
                            children=[
                                html.Label("Deportista"),
                                dcc.Dropdown(
                                    id="sel-user-sens",
                                    options=options_users,
                                    placeholder="Selecciona deportista...",
                                ),
                                html.Div(className="spacer-12"),
                                html.Label("Sensores asignados"),
                                checklist,
                                html.Div(className="spacer-12"),
                                html.Button(
                                    "Guardar asignación",
                                    id="btn-save-sens",
                                    className="btn btn-primary",
                                ),
                            ],
                        ),
                        html.Div(
                            className="card",
                            children=[
                                html.Label("Información de sensores asignados"),
                                html.Div(id="sensor-info", className="sensor-info"),
                            ],
                        ),
                    ],
                ),
                html.Div(
                    id="sens-msg",
                    className="msg msg-error",
                ),
            ],
        )

    # ---------- Layout DEPORTISTA ----------

    def _layout_athlete(self):
        user_id = session.get("user_id")
        if not user_id:
            return html.Div("No se encontró tu sesión de deportista.")

        try:
            codes = self.db.get_user_sensors(int(user_id)) or []
        except Exception:
            codes = []

        cards = self._build_sensor_cards(codes, user_id_for_last_metrics=int(user_id))

        if not cards:
            cards = [
                html.Div(
                    "Todavía no tienes sensores asignados. Tu coach puede asignarlos desde su panel.",
                    className="muted",
                )
            ]

        return html.Div(
            className="sensors-page",
            children=[
                html.Div(
                    className="page-head",
                    children=[
                        html.H2("Mis sensores"),
                        html.Div(
                            "Tu coach gestiona la asignación de sensores. Aquí solo ves lo que tienes asociado.",
                            className="page-sub",
                        ),
                    ],
                ),
                html.Div(className="ecg-divider"),
                html.Div(cards, className="sensor-cards"),
                # ✅ placeholders invisibles para evitar errores de callbacks en modo deportista
                self._hidden_placeholders_for_callbacks(),
            ],
        )

    # ---------- Callbacks ----------

    def _register_callbacks(self):
        app = self.app
        db = self.db

        @app.callback(
            Output("chk-sensors", "value"),
            Input("sel-user-sens", "value"),
            prevent_initial_call=True,
        )
        def load_user_sensors(user_id):
            if not user_id:
                raise PreventUpdate
            try:
                return db.get_user_sensors(int(user_id)) or []
            except Exception:
                return []

        # ✅ Panel en vivo (sin guardar)
        @app.callback(
            Output("sensor-info", "children"),
            Input("sel-user-sens", "value"),
            Input("chk-sensors", "value"),
            prevent_initial_call=True,
        )
        def live_info_box(user_id, codes):
            if not user_id:
                return []
            try:
                # Reutiliza el builder de cards
                return self._build_sensor_cards(codes or [], user_id_for_last_metrics=int(user_id))
            except Exception:
                return []

        # ✅ Guardar asignación (SOLO msg) -> evita outputs duplicados
        @app.callback(
            Output("sens-msg", "children"),
            Input("btn-save-sens", "n_clicks"),
            State("sel-user-sens", "value"),
            State("chk-sensors", "value"),
            prevent_initial_call=True,
        )
        def save_user_sensors(n, user_id, codes):
            role = str(session.get("role") or "no autenticado")

            if role not in ("coach", "admin"):
                return "No tienes permisos para modificar sensores."

            if not user_id:
                return "Selecciona usuario."

            try:
                db.set_user_sensors(int(user_id), codes or [])
            except Exception:
                return "Error guardando sensores (DB)."

            return "Asignación guardada."
