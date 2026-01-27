from chatwithai import *
from maintextandspeech import *

if __name__ == "__main__":
    user_speech = speech_to_text()
    
    if user_speech:  # Check if not None or "exit"
        llm_response = ask_llm(user_speech+" via the underground, short and simple answer, no *")
        text_to_speech(llm_response)
    else:
        print("No input received or exit command given.")