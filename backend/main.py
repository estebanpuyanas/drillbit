from fastapi import FastAPI
from openai import OpenAI
from chroma import client, collection

app = FastAPI()
llm = OpenAI(base_url="http://ramalama:8080/v1", api_key="unused")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/test-llm")
async def test_llm():
    response = llm.chat.completions.create(
        model="llama3.2:3b",
        messages=[{"role": "user", "content": "Name one Linux video editing package."}],
    )
    return {"response": response.choices[0].message.content}
