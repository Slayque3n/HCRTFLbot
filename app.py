from flask import Flask, request, jsonify, send_file
from chatwithai import *
from maintextandspeech import *

app = Flask(__name__)

@app.route("/")
def index():
    return send_file("index.html")

@app.route("/talk", methods=["POST"])
def talk():
    data = request.json
    language = data["language"]
    user_text = speech_to_text(language)

    llm_response = ask_llm(
        user_text +
        " via the underground, short and simple answer, no *, answer in " +
        language
    )

    filename = "output.mp3"
    text_to_speech(llm_response, language, filename)

    return jsonify({
        "heard_text": user_text,
        "response_text": llm_response,
        "audio": filename
    })

if __name__ == "__main__":
    app.run(debug=True)
