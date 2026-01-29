from flask import Flask, render_template, request, redirect, url_for
from chatwithai import *
from maintextandspeech import *

app = Flask(__name__)

@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        language = request.form["language"]
        return redirect(url_for("speak", language=language))
    return render_template("index.html")

@app.route("/speak", methods=["GET", "POST"])
def speak():
    language = request.args.get("language", "en-US")
    heard = ""
    response = ""

    if request.method == "POST":
        heard = speech_to_text(language)
        response = ask_llm(
            heard +
            " via the underground, short and simple answer, no *, answer in " +
            language
        )
        text_to_speech(response, language)

    return render_template(
        "speak.html",
        language=language,
        heard=heard,
        response=response
    )

if __name__ == "__main__":
    app.run(debug=True)
