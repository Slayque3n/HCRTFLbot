from flask import Flask, render_template, request, redirect, url_for, jsonify
from chatwithai import ask_llm
from maintextandspeech import speech_to_text, text_to_speech, findkeywords, get_next_letter
from ros_publisher import get_ros_publisher, shutdown_ros
import atexit


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

LANGUAGE_NAMES = {
    "en-US": "English",
    "de-DE": "German",
    "fr-FR": "French",
    "es-ES": "Spanish",
    "it-IT": "Italian",
    "ru-RU": "Russian",
}

app = Flask(__name__)

# Create ROS publisher once
ros_publisher = get_ros_publisher()

# Clean shutdown when Flask exits
atexit.register(shutdown_ros)


@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        language = request.form["language"]
        return redirect(url_for("speak", language=language))

    ui = translations["en-US"]
    return render_template("index.html", ui=ui)


@app.route("/speak", methods=["GET", "POST"])
def speak():
    language = request.args.get("language", "en-US")
    heard = ""
    response = ""

    if request.method == "POST":
        heard = speech_to_text(language)

        language_name = LANGUAGE_NAMES.get(language, "English")

        prompt = (
            f"{heard} via the underground. "
            f"Short answer. Tell me direction and line only. "
            f"No bullet points. Answer in {language_name}."
        )

        #response = ask_llm(prompt)
        response = "data: hello"

        # Publish the raw LLM response to ROS 2
        ros_publisher.publish_response(response)

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


@app.route("/bsl")
def bsl():
    return render_template("bsl.html")


@app.route("/bsl/spell")
def bsl_spell():
    return render_template("bsl_spell.html")


@app.route("/bsl/spell/letter")
def bsl_spell_letter():
    letter = get_next_letter()
    return jsonify({"letter": letter})


@app.route("/bsl/result", methods=["POST"])
def bsl_result():
    station = request.form.get("station", "").strip()

    response = ask_llm(
        f"How do I get to {station} station via the London Underground? "
        f"Short answer, tell me the direction and line, no bullet points."
    )

    # Publish BSL route result too
    ros_publisher.publish_response(response)

    return render_template("bsl_result.html", station=station, response=response)


if __name__ == "__main__":
    app.run(host="0.0.0.0", debug=True)