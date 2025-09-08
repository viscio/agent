import os
from typing import Optional
from openai import OpenAI

#BASE_URL = os.getenv("VLLM_BASE_URL", "http://localhost:8000/v1")
BASE_URL = "http://131.175.207.215:8000/v1"

#API_KEY = os.getenv("VLLM_API_KEY", "local-key")
API_KEY = "hXpaz5&knrmJLsC5><Jrbgn"

#MODEL = os.getenv("VLLM_MODEL", "meta-llama/Meta-Llama-3.1-8B-Instruct")
MODEL = "Mistral-Small-3.2-24B-Instruct-2506"

client = OpenAI(base_url=BASE_URL, api_key=API_KEY)

def ask_with_llm(prompt: str) -> Optional[str]:
    """
    Send a simple single-turn prompt to the local vLLM server
    using the OpenAI-compatible Chat Completions API.
    """
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=float(os.getenv("VLLM_TEMPERATURE", "0.7")),
        max_tokens=int(os.getenv("VLLM_MAX_TOKENS", "512")),
    )
    return resp.choices[0].message.content if resp.choices else None
