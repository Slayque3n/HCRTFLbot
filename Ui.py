import tkinter as tk
from chatwithai import *
from maintextandspeech import *

LANGUAGES = {
    "Russian": "ru-RU",
    "English": "en-US",
    "German": "de-DE",
    "French": "fr-FR",
    "Spanish": "es-ES",
    "Italian": "it-IT"
}

selected_language = "en-US"


def set_language(lang_code):
    global selected_language
    selected_language = lang_code
    status_label.config(text=f"Language set to {lang_code}")


def start_conversation():
    status_label.config(text="Listening...")
    user_speech = speech_to_text(selected_language)

    if user_speech:
        status_label.config(text="Thinking...")
        llm_response = ask_llm(
            user_speech +
            " via the underground, short and simple answer, no *, answer in " +
            selected_language
        )
        text_to_speech(llm_response, selected_language)
        status_label.config(text="Done")
    else:
        status_label.config(text="No input received")


# --- UI ---
root = tk.Tk()
root.title("Voice Assistant")

tk.Label(root, text="Choose Language").pack(pady=5)

for name, code in LANGUAGES.items():
    tk.Button(
        root,
        text=name,
        width=20,
        command=lambda c=code: set_language(c)
    ).pack(pady=2)

tk.Button(
    root,
    text="🎤 Speak",
    width=25,
    height=2,
    command=start_conversation
).pack(pady=15)

status_label = tk.Label(root, text="Ready")
status_label.pack(pady=5)

root.mainloop()
