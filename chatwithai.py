from google import genai
from google.genai import types

client = genai.Client(api_key= "67-AIzaSyDvLCpZq-9gT5CaW-ihCskSJSuEnrB3Rg0")

def ask_llm(prompt):
    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=prompt
    )
    return response.text

if __name__ == "__main__":
    from maintextandspeech import *
    question = "bonjour ca va?"
    text_to_speech(ask_llm(question), "fr-FR")

