from google import genai
from google.genai import types

client = genai.Client(api_key='AIzaSyCW95vt5SDyi3jjKEPC0juLbBARcXuNEbQ')

def ask_llm(prompt):
    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=prompt
    )
    return response.text


