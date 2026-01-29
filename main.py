from chatwithai import *
from maintextandspeech import *

if __name__ == "__main__":
    language = ""
    languages = ["ru-RU", "en-US", "de-DE", "fr-FR", "es-ES", "it-IT"] 
    while language not in languages:
        language = input("Enter the language code (e.g. ru-RU, en-US, de-DE, fr-FR, es-ES, it-IT): ")

    user_speech = speech_to_text(language)
    
    if user_speech:  # Check if not None or "exit"
        llm_response = ask_llm(user_speech+" via the underground, short and simple answer, no *, answer in "+language)
        text_to_speech(llm_response, language)
    else:
        print("No input received or exit command given.")