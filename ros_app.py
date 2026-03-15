from flask import Flask, render_template, request, redirect, url_for, jsonify
from chatwithai import *
from maintextandspeech import speech_to_text, text_to_speech, findkeywords, get_next_letter

import rclpy
from rclpy.node import Node
from std_msgs.msg import String

from threading import Thread, Lock
import atexit


bsl_info = None
flask_node = None
msg_lock = Lock()  # for thread-safe access to latest_message
ros_station_response = None

class FlaskNode(Node):

    def __init__(self):
        super().__init__('flask_node')
        self.subscription = self.create_subscription(
            String,
            'bsl_data',
            self.listener_callback,
            10)
        self.subscription  # prevent unused variable warning

    def listener_callback(self, msg):
        global bsl_info
        with msg_lock:
            bsl_info = msg.data
        #self.get_logger().info('I heard: "%s"' % msg.data)

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



def handle_bsl_command(command):
    global ros_station_response  # needed because we assign to it

    match command:
        case "One - 1":
            ros_station_response = "King's Cross St. Pancras"
        case "Two -2":
            ros_station_response = "Waterloo"
        case "Three - 3":
            ros_station_response = "Victoria"
        case "Four - 4":
            ros_station_response = "Liverpool Street"
        case "Five - 5":
            ros_station_response = "London Bridge"
        case "Six - 6":
            ros_station_response = "Paddington"
        case "Seven - 7":
            ros_station_response = "Bank / Monument"
        case "Eight - 8":
            ros_station_response = "Stratford"
        case "Nine - 9":
            ros_station_response = "Canary Wharf"
        case "Ten - 10":
            ros_station_response = None
        case _:  # default
            ros_station_response = None

    return ros_station_response





app = Flask(__name__)

#ROS Init

def ros_thread_func():
    global node
    rclpy.init()
    flask_node = FlaskNode()
    rclpy.spin(flask_node)

# Start ROS in a daemon thread so Flask can run in main thread
ros_thread = Thread(target=ros_thread_func, daemon=True)
ros_thread.start()


#ROS Shutdown Procedure

@atexit.register
def shutdown_ros():
    global flask_node
    if flask_node is not None:
        flask_node.destroy_node()
    rclpy.shutdown()


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

@app.route("/bsl")
def bsl():
    with msg_lock:
       msg = bsl_info

    handle_bsl_command(msg)
    #print("DEBUG:" + bsl_info)
    #print(ros_station_response)
    if ros_station_response is not None:
        return redirect(url_for('bsl_result'))

    return render_template("bsl.html")

@app.route("/bsl/spell")
def bsl_spell():
    return render_template("bsl_spell.html")

@app.route("/bsl/spell/letter")
def bsl_spell_letter():
    letter = get_next_letter()
    return jsonify({"letter": letter})

@app.route("/bsl/result", methods=["POST","GET"])
def bsl_result():
    if ros_station_response is None:
        station = request.form.get("station", "").strip()
    else:
        station = ros_station_response
    response = ask_llm(
        "How do I get to " + station +
        " station via the London Underground? Short answer, tell me the direction and line, no *."
    )
    return render_template("bsl_result.html", station=station, response=response)

if __name__ == "__main__":
    app.run(host="0.0.0.0", debug=True)
