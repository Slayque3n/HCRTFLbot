import speech_recognition as sr
import usb.core
# import usb.util
# import time
from tuning import Tuning
import time


# dev = usb.core.find(idVendor=0x2886, idProduct=0x0018)
start = time.time()
RESPEAKER_INDEX = 1 

r = sr.Recognizer()

# Define the microphone source
# The 'sample_rate' should ideally match the hardware (16000 or 44100)
mic = sr.Microphone(device_index=RESPEAKER_INDEX, sample_rate=16000)

with mic as source:
    print("--- ReSpeaker is Calibrating for Background Noise ---")
    r.adjust_for_ambient_noise(source, duration=2)
    
    print("--- Ready! Say something ---")
    audio = r.listen(source)
    # if dev:
    #     Mic_tuning = Tuning(dev)


try:
    print("Recognizing...")
    # Uses Google's free web search API (default)
    text = r.recognize_google(audio)
    print(f"You said: {text}")
    elapsed = time.time()
    print(f"Spent time: {elapsed - start}")
    # print (f"Direction of audio: {Mic_tuning.direction}")

except sr.UnknownValueError:
    print("Google Speech Recognition could not understand the audio.")
except sr.RequestError as e:
    print(f"Could not request results from Google; {e}")