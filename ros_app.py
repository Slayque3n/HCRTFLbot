from flask import Flask, render_template, request, redirect, url_for, jsonify
from chatwithai import *
from maintextandspeech import speech_to_text, text_to_speech, findkeywords, get_next_letter

import rclpy
from rclpy.node import Node
from std_msgs.msg import String
import json
import threading
from threading import Thread, Lock
import atexit
import re


bsl_info = None
flask_node = None
msg_lock = Lock() 
node_lock = Lock()
ros_station_response = None
platform_info = None
bsl_redirect = False
LANGUAGE_NAME_MAP = {
    "en-US": "English",
    "de-DE": "German",
    "fr-FR": "French",
    "es-ES": "Spanish",
    "it-IT": "Italian",
    "ru-RU": "Russian",
}

class FlaskNode(Node):

    def __init__(self):
        super().__init__('flask_node')
        self.platform_publisher_ = self.create_publisher(String, '/platform_name', 10)
        self.llm_response_publisher = self.create_publisher(String, '/llm_topic', 10)
        self.subscription = self.create_subscription(
            String,
            'bsl_data',
            self.listener_callback,
            10)
        self.subscription


    def listener_callback(self, msg):
        global bsl_info, bsl_redirect
        with msg_lock:
            if bsl_redirect:
                bsl_info = None
            else:
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


def extract_station_platform_guidance(llm_response: str):
    station = None
    platform = None
    guidance = llm_response.strip()

    station_match = re.search(r"STATION:\s*(.*)", llm_response, re.IGNORECASE)
    platform_match = re.search(r"PLATFORM:\s*([a-z_]+)", llm_response, re.IGNORECASE)
    guidance_match = re.search(r"GUIDANCE:\s*(.*)", llm_response, re.IGNORECASE | re.DOTALL)

    if station_match:
        station = station_match.group(1).strip()

    if platform_match:
        platform = platform_match.group(1).strip().lower()

    if guidance_match:
        guidance = guidance_match.group(1).strip()

    return station, platform, guidance

def handle_bsl_command(command):
    global ros_station_response  

    with msg_lock:
        match command:
            case "BSL (1-Hand): One - 1":
                ros_station_response = "King's Cross St. Pancras"
            case "BSL (1-Hand): Two - 2":
                ros_station_response = "Waterloo"
            case "BSL (1-Hand): Three - 3":
                ros_station_response = "Victoria"
            case "BSL (1-Hand): Four - 4":
                ros_station_response = "Liverpool Street"
            case "BSL (1-Hand): Five - 5":
                ros_station_response = "London Bridge"
            case "BSL (1-Hand): Six - 6":
                ros_station_response = "Paddington"
            case "BSL (1-Hand): Seven - 7":
                ros_station_response = "Bank / Monument"
            case "BSL (1-Hand): Eight - 8":
                ros_station_response = "Stratford"
            case "BSL (1-Hand): Nine - 9":
                ros_station_response = "Canary Wharf"
            case "BSL (2-Hand): Ten - 10":
                ros_station_response = None
            case _:  # default
                ros_station_response = None

    return ros_station_response





app = Flask(__name__)

#ROS Init

def ros_thread_func():
    global flask_node
    rclpy.init()
    flask_node = FlaskNode()
    rclpy.spin(flask_node)

# Start ROS in a daemon thread so Flask can run in main thread
ros_thread = Thread(target=ros_thread_func, daemon=True)
ros_thread.start()

def publish_llm_payload(payload: dict):
    global flask_node
    if flask_node is None:
        return
    ros_msg = String()
    ros_msg.data = json.dumps(payload)
    flask_node.llm_response_publisher.publish(ros_msg)

#ROS Shutdown Procedure

@atexit.register
def shutdown_ros():
    global flask_node
    if flask_node is not None:
        flask_node.destroy_node()
    rclpy.shutdown()

@app.route("/", methods=["GET", "POST", "HEAD"])
def index():
    if request.method == "POST":
        language = request.form["language"]
        return redirect(url_for("speak", language=language))

    ui = translations["en-US"]

    if request.method == "HEAD":
        return "", 200

    publish_llm_payload({
        "type": "main_menu",
        "text": "Hello. How can I help you?"
    })

    return render_template("index.html", ui=ui)
@app.route("/speak", methods=["GET", "POST"])
def speak():
    global flask_node
    language = request.args.get("language", "en-US")
    heard = ""
    response = ""
    guidance = ""
    platform = None

    if request.method == "POST":
        heard = speech_to_text(language)

        if not heard:
            publish_llm_payload({
                "type": "didnt_hear",
                "text": "Sorry, I didn't catch that. Please say that again.",
                "language": language
            })
            return render_template(
                "speak.html",
                language=language,
                heard="",
                response="No speech detected.",
                ui=translations.get(language, translations["en-US"])
            )
        publish_llm_payload({
            "type": "thinking_start"
        })
        lang_name = LANGUAGE_NAME_MAP.get(language, "English")
        try:
            response = ask_llm(
                f"How do I get to {heard} from South Kensington via the underground? "
                f"You are a station guide. "
                f"Return the answer in exactly this format:\n"
                f"STATION: <station_name>\n"
                f"PLATFORM: <platform_name>\n"
                f"GUIDANCE: <short spoken guidance>\n\n"
                f"The platform_name must be one of:\n"
                f"piccadilly_westbound, piccadilly_eastbound, "
                f"district_eastbound, district_westbound, "
                f"circle_eastbound, circle_westbound.\n\n"
                f"The station_name must be the end station I want to get to from South Kensington\n"
                f"Example:\n"
                f"STATION: King's Cross St. Pancras.\n"
                f"PLATFORM: piccadilly_eastbound\n"
                f"GUIDANCE: From South Kensington, head to the Piccadilly Line platforms."
                f"Take a train from the eastbound platform toward Cockfosters directly to King's Cross St. Pancras.\n\n"
                f"Respond in {lang_name} for the GUIDANCE line, but keep the PLATFORM value in lowercase underscore format."
            )
            station, platform, guidance = extract_station_platform_guidance(response)
            ALLOWED_PLATFORMS = {
                "piccadilly_westbound",
                "piccadilly_eastbound",
                "district_eastbound",
                "district_westbound",
                "circle_eastbound",
                "circle_westbound",
            }
            if platform not in ALLOWED_PLATFORMS:
                platform = None
            if not guidance:
                guidance = response.strip()
        finally:
            publish_llm_payload({
                "type": "thinking_stop"
            })
        findkeywords(guidance)
        #text_to_speech(response, language)
        publish_llm_payload({
            "type": "station_guidance",
            "station": station or heard,
            "platform": platform,
            "text": guidance,
            "language": language
        })
    ui = translations.get(language, translations["en-US"])

    return render_template(
        "speak.html",
        language=language,
        heard=heard,
        response=guidance,
        ui=ui
    )

@app.route("/bsl")
def bsl():
    return render_template("bsl.html")

@app.route("/bsl/check")
def bsl_check():
    global bsl_info, bsl_redirect
    with msg_lock:
        msg = bsl_info

    valid = handle_bsl_command(msg)

    if valid is not None:
        with msg_lock:
            bsl_redirect = True
        return jsonify({"valid": True})

    return jsonify({"valid": False})

@app.route("/bsl/spell")
def bsl_spell():
    return render_template("bsl_spell.html")

@app.route("/bsl/spell/letter")
def bsl_spell_letter():
    letter = get_next_letter()
    return jsonify({"letter": letter})

@app.route("/bsl/result", methods=["POST","GET"])
def bsl_result():
    global bsl_redirect

    if ros_station_response is None:
        station = request.form.get("station", "").strip()
    else:
        station = ros_station_response
        
    publish_llm_payload({"type": "thinking_start"})
    
    try:
        response = ask_llm(
                    f"How do I get to {station} from South Kensington via the underground? "
                        f"You are a station guide. "
                        f"Return the answer in exactly this format:\n"
                        f"STATION: <station_name>\n"
                        f"PLATFORM: <platform_name>\n"
                        f"GUIDANCE: <short spoken guidance>\n\n"
                        f"The platform_name must be one of:\n"
                        f"piccadilly_westbound, piccadilly_eastbound, "
                        f"district_eastbound, district_westbound, "
                        f"circle_eastbound, circle_westbound.\n\n"
                        f"The station_name must be the end station I want to get to from South Kensington\n"
                        f"Example:\n"
                        f"STATION: King's Cross St. Pancras.\n"
                        f"PLATFORM: piccadilly_eastbound\n"
                        f"GUIDANCE: From South Kensington, head to the Piccadilly Line platforms."
                        f"Take a train from the eastbound platform toward Cockfosters directly to King's Cross St. Pancras.\n\n"
                        f"Respond in en-US for the GUIDANCE line, but keep the PLATFORM value in lowercase underscore format."
                    )

        station, platform, guidance = extract_station_platform_guidance(response)
        
        if not guidance:
            guidance = response.strip()
        
        response = guidance
    finally:
        publish_llm_payload({"type": "thinking_stop"})
    

    publish_llm_payload({
        "type": "station_guidance",
        "station": station,
        "platform": platform,
        "text": guidance,
        "language": "en-US"
    })
    with msg_lock:
        bsl_redirect = False
    return render_template("bsl_result.html", station=station, response=guidance)

if __name__ == "__main__":
    app.run(host="0.0.0.0", debug=True)
