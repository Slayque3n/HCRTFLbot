from flask import Flask, render_template, request, redirect, url_for
from chatwithai import *
from maintextandspeech import *


translations = {
    "en-US": {
        "select_language": "Select a language",
        "speak": "Speak",
        "language": "Language",
        "text_heard": "Text heard",
        "llm_response": "LLM response",
        "change_language": "Change language"
    },
    "de-DE": {
        "select_language": "Sprache auswählen",
        "speak": "Sprechen",
        "language": "Sprache",
        "text_heard": "Verstandener Text",
        "llm_response": "LLM Antwort",
        "change_language": "Sprache ändern"
    },
    "fr-FR": {
        "select_language": "Choisir une langue",
        "speak": "Parler",
        "language": "Langue",
        "text_heard": "Texte entendu",
        "llm_response": "Réponse du LLM",
        "change_language": "Changer de langue"
    },
    "es-ES": {
        "select_language": "Seleccionar idioma",
        "speak": "Hablar",
        "language": "Idioma",
        "text_heard": "Texto escuchado",
        "llm_response": "Respuesta del LLM",
        "change_language": "Cambiar idioma"
    },
    "it-IT": {
        "select_language": "Seleziona lingua",
        "speak": "Parla",
        "language": "Lingua",
        "text_heard": "Testo ascoltato",
        "llm_response": "Risposta LLM",
        "change_language": "Cambia lingua"
    },
    "ru-RU": {
        "select_language": "Выберите язык",
        "speak": "Говорить",
        "language": "Язык",
        "text_heard": "Распознанный текст",
        "llm_response": "Ответ LLM",
        "change_language": "Сменить язык"
    }
}








app = Flask(__name__)

@app.route("/", methods=["GET", "POST"])
@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        language = request.form["language"]
        return redirect(url_for("speak", language=language))

    # Default UI language
    ui = translations["en-US"]

    return render_template("index.html", ui=ui)
@app.route("/speak", methods=["GET", "POST"])
@app.route("/speak", methods=["GET", "POST"])
def speak():
    language = request.args.get("language", "en-US")
    heard = ""
    response = ""

    if request.method == "POST":
        heard = speech_to_text(language)

        response = ask_llm(
            heard +
            " via the underground, short answer, tell me direction and line, no *, answer in " +
            language
        )
        findkeywords(response)
        text_to_speech(response, language)

    ui = translations.get(language, translations["en-US"])

    return render_template(
        "speak.html",
        language=language,
        heard=heard,
        response=response,
        ui=ui
    )

if __name__ == "__main__":
    app.run(host="0.0.0.0", debug=True)
