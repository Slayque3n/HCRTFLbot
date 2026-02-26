from gtts import gTTS
import os
import speech_recognition as sr
import usb.core
from tuning import Tuning

TTS_LANG_MAP = {
    "en-US": "en",
    "ru-RU": "ru",
    "de-DE": "de",
    "fr-FR": "fr",
    "es-ES": "es",
    "it-IT": "it"
}

# Tube lines (keywords to search in lines)
keywordsline = [
    "Bakerloo", "Central", "Circle", "District", "Hammersmith & City",
    "Jubilee", "Metropolitan", "Northern", "Piccadilly", "Victoria", "Waterloo & City", "London Overground", "DLR", "Docklands Light Railway"
]

# Directions (keywords to search for)
keywordsdirection = [
    "north", "south", "east", "west",
    "northbound", "southbound", "eastbound", "westbound",
    "towards north", "towards south", "towards east", "towards west"
]


def text_to_speech(text, language,filename="output.mp3", slow=False):
    tts_lang = TTS_LANG_MAP.get(language, "en")
    tts = gTTS(text=text, lang=tts_lang, slow=slow)
    tts.save(filename)
    os.system(f"start {filename}")  # Windows only

def text_to_speech(text, language, filename="output.mp3", slow=False):
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


def speech_to_text(language):
    dev = usb.core.find(idVendor=0x2886, idProduct=0x0018)
    RESPEAKER_INDEX = 1 

    r = sr.Recognizer()
    r.pause_threshold = 2  # Wait 2 seconds of silence before considering phrase complete
    mic = sr.Microphone(device_index=RESPEAKER_INDEX, sample_rate=16000)

    while True:
        try:    
            with mic as source:
                print("--- ReSpeaker is Calibrating for Background Noise ---")
                r.adjust_for_ambient_noise(source, duration=2)
                
                print("--- Ready! Say something ---")
                audio = r.listen(source)
                if dev:
                    Mic_tuning = Tuning(dev)

            print("Recognizing...")
            text = r.recognize_google(audio)
            print(f"You said: {text}")
            print (f"Direction of audio: {Mic_tuning.direction}")

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

def findkeywords(heardspeech):
    # Initialize variables
    found_line = None
    found_direction = None
    # Initialize results
    found_line = None
    found_direction = None

    # Scan the string for the first direction mentioned
    for direction in keywordsdirection:
        
        if direction in heardspeech.lower():  # lowercase for case-insensitive match
            found_direction = direction
            break


    # Scan the string for the first tube line mentioned
    for line in keywordsline:
        if line in heardspeech:
            found_line = line
            break

    
    print("First Tube line mentioned:", found_line)
    print("First direction mentioned:", found_direction)



if __name__ == "__main__":
    language = ""
    languages = ["ru-RU", "en-US", "de-DE", "fr-FR", "es-ES", "it-IT"] 
    while language not in languages:
        language = input("Enter the language code (e.g. ru-RU, en-US, de-DE, fr-FR, es-ES, it-IT): ")

    speech_to_text(language)
