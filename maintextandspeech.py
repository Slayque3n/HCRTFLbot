from gtts import gTTS
import os
import speech_recognition as sr

def text_to_speech(text, language="en", filename="output.mp3", slow=False):
    """
    Converts text to speech and plays the audio.

    :param text: Text to convert to speech
    :param language: Language code (default: 'en')
    :param filename: Output mp3 file name
    :param slow: Speak slowly if True
    """
    tts = gTTS(text=text, lang=language, slow=slow)
    tts.save(filename)
    os.system(f"start {filename}")  # Windows only


def speech_to_text():
    r = sr.Recognizer()
    r.pause_threshold = 1  # Wait 2 seconds of silence before considering phrase complete

    while True:
        try:
            with sr.Microphone() as source:
                print("Listening...")
                r.adjust_for_ambient_noise(source, duration=0.2)
                audio = r.listen(source,  timeout=90, phrase_time_limit=30)

            text = r.recognize_google(audio).lower()
            print("You said:", text)
            

            if "exit" in text:
                print("Exiting program...")
                return None
            return text

        except sr.RequestError as e:
            print(f"Could not request results; {e}")

        except sr.UnknownValueError:
            print("Could not understand audio")

        except KeyboardInterrupt:
            print("Program terminated by user")
            break


if __name__ == "__main__":
    speech_to_text()
