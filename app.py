from flask import Flask, render_template, request, redirect, url_for, jsonify
from chatwithai import *
from maintextandspeech import speech_to_text, text_to_speech, findkeywords, get_next_letter


# ---------- UI translations ----------
translations = {
    "en-US": {
        "select_language":  "Select a language",
        "speak":            "Speak",
        "language":         "Language",
        "text_heard":       "Text heard",
        "llm_response":     "LLM response",
        "change_language":  "Change language",
        "select_station":   "Choose a Station",
        "station_subtitle": "Select a London Underground station",
        "other":            "Other",
        "other_placeholder":"e.g. Oxford Circus",
        "go":               "Go",
        "back_languages":   "← Back to language select",
        "back_stations":    "← Back to stations",
        "directions_for":   "Directions to",
    },
    "de-DE": {
        "select_language":  "Sprache auswählen",
        "speak":            "Sprechen",
        "language":         "Sprache",
        "text_heard":       "Verstandener Text",
        "llm_response":     "LLM Antwort",
        "change_language":  "Sprache ändern",
        "select_station":   "Bahnhof wählen",
        "station_subtitle": "Wählen Sie eine U-Bahn-Station in London",
        "other":            "Andere",
        "other_placeholder":"z.B. Oxford Circus",
        "go":               "Los",
        "back_languages":   "← Zurück zur Sprachauswahl",
        "back_stations":    "← Zurück zu den Stationen",
        "directions_for":   "Wegbeschreibung zu",
    },
    "fr-FR": {
        "select_language":  "Choisir une langue",
        "speak":            "Parler",
        "language":         "Langue",
        "text_heard":       "Texte entendu",
        "llm_response":     "Réponse du LLM",
        "change_language":  "Changer de langue",
        "select_station":   "Choisir une station",
        "station_subtitle": "Sélectionnez une station du métro de Londres",
        "other":            "Autre",
        "other_placeholder":"ex. Oxford Circus",
        "go":               "Aller",
        "back_languages":   "← Retour au choix de la langue",
        "back_stations":    "← Retour aux stations",
        "directions_for":   "Itinéraire vers",
    },
    "es-ES": {
        "select_language":  "Seleccionar idioma",
        "speak":            "Hablar",
        "language":         "Idioma",
        "text_heard":       "Texto escuchado",
        "llm_response":     "Respuesta del LLM",
        "change_language":  "Cambiar idioma",
        "select_station":   "Seleccionar estación",
        "station_subtitle": "Seleccione una estación del metro de Londres",
        "other":            "Otra",
        "other_placeholder":"p.ej. Oxford Circus",
        "go":               "Ir",
        "back_languages":   "← Volver a selección de idioma",
        "back_stations":    "← Volver a las estaciones",
        "directions_for":   "Cómo llegar a",
    },
    "it-IT": {
        "select_language":  "Seleziona lingua",
        "speak":            "Parla",
        "language":         "Lingua",
        "text_heard":       "Testo ascoltato",
        "llm_response":     "Risposta LLM",
        "change_language":  "Cambia lingua",
        "select_station":   "Seleziona stazione",
        "station_subtitle": "Seleziona una stazione della metropolitana di Londra",
        "other":            "Altra",
        "other_placeholder":"es. Oxford Circus",
        "go":               "Vai",
        "back_languages":   "← Torna alla selezione della lingua",
        "back_stations":    "← Torna alle stazioni",
        "directions_for":   "Come arrivare a",
    },
    "ru-RU": {
        "select_language":  "Выберите язык",
        "speak":            "Говорить",
        "language":         "Язык",
        "text_heard":       "Распознанный текст",
        "llm_response":     "Ответ LLM",
        "change_language":  "Сменить язык",
        "select_station":   "Выберите станцию",
        "station_subtitle": "Выберите станцию лондонского метро",
        "other":            "Другая",
        "other_placeholder":"напр. Оксфорд-Серкус",
        "go":               "Перейти",
        "back_languages":   "← К выбору языка",
        "back_stations":    "← К станциям",
        "directions_for":   "Маршрут до",
    },
}

# ---------- Station lists (display name, English value for LLM) ----------
STATIONS_DEFAULT = [
    ("King's Cross St. Pancras", "King's Cross St. Pancras"),
    ("Waterloo",                 "Waterloo"),
    ("Victoria",                 "Victoria"),
    ("Liverpool Street",         "Liverpool Street"),
    ("London Bridge",            "London Bridge"),
    ("Paddington",               "Paddington"),
    ("Bank / Monument",          "Bank"),
    ("Stratford",                "Stratford"),
    ("Canary Wharf",             "Canary Wharf"),
    ("Oxford Circus",            "Oxford Circus"),
]

STATIONS = {
    "ru-RU": [
        ("Кингс-Кросс Сент-Панкрас", "King's Cross St. Pancras"),
        ("Ватерлоо",                  "Waterloo"),
        ("Виктория",                  "Victoria"),
        ("Ливерпуль-Стрит",           "Liverpool Street"),
        ("Лондон-Бридж",              "London Bridge"),
        ("Паддингтон",                "Paddington"),
        ("Бэнк / Монумент",           "Bank"),
        ("Стратфорд",                 "Stratford"),
        ("Канэри-Уорф",               "Canary Wharf"),
        ("Оксфорд-Серкус",            "Oxford Circus"),
    ],
}


app = Flask(__name__)


# --- 1. Language select (home) ---
@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        language = request.form["language"]
        return redirect(url_for("stations", language=language))
    return render_template("index.html", ui=translations["en-US"])


# --- 2. Station picker ---
@app.route("/stations")
def stations():
    language = request.args.get("language", "en-US")
    ui = translations.get(language, translations["en-US"])
    station_list = STATIONS.get(language, STATIONS_DEFAULT)
    return render_template("stations.html", language=language, ui=ui, stations=station_list)


# --- 3. Station result (ask LLM) ---
@app.route("/stations/result", methods=["POST"])
def stations_result():
    station   = request.form.get("station", "").strip()
    language  = request.form.get("language", "en-US")
    ui        = translations.get(language, translations["en-US"])
    response  = ask_llm(
        "How do I get to " + station +
        " station via the London Underground? Short answer, tell me the direction and line, no *."
        " Answer in " + language
    )
    return render_template("station_result.html", station=station, response=response, ui=ui, language=language)


# --- 4. BSL: skip stations, go straight to spell ---
@app.route("/bsl")
def bsl():
    return redirect(url_for("bsl_spell"))


# --- 5. BSL spell page ---
@app.route("/bsl/spell")
def bsl_spell():
    return render_template("bsl_spell.html")


# --- 6. BSL letter polling endpoint ---
@app.route("/bsl/spell/letter")
def bsl_spell_letter():
    letter = get_next_letter()
    return jsonify({"letter": letter})


# --- 7. BSL result (ask LLM) ---
@app.route("/bsl/result", methods=["POST"])
def bsl_result():
    station  = request.form.get("station", "").strip()
    response = ask_llm(
        "How do I get to " + station +
        " station via the London Underground? Short answer, tell me the direction and line, no *."
    )
    return render_template("bsl_result.html", station=station, response=response)


# --- Legacy speak route (kept for reference) ---
@app.route("/speak", methods=["GET", "POST"])
def speak():
    language = request.args.get("language", "en-US")
    heard = ""
    response = ""
    if request.method == "POST":
        heard    = speech_to_text(language)
        response = ask_llm(
            heard +
            " via the underground, short answer, tell me direction and line, no *, answer in " +
            language
        )
        findkeywords(response)
        text_to_speech(response, language)
    ui = translations.get(language, translations["en-US"])
    return render_template("speak.html", language=language, heard=heard, response=response, ui=ui)


if __name__ == "__main__":
    app.run(host="0.0.0.0", debug=True)
