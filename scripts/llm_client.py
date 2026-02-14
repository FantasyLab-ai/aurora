import os, requests

def ollama(prompt, max_tokens=120, timeout=20):
    url=os.getenv("OLLAMA_URL","http://127.0.0.1:11434")+"/api/generate"
    model=os.getenv("OLLAMA_MODEL","qwen2:1.5b")
    r=requests.post(url,json={"model":model,"prompt":prompt,"stream":False,
                             "options":{"num_ctx":512,"num_predict":max_tokens}},
                    timeout=timeout)
    r.raise_for_status()
    return r.json().get("response","").strip()

def groq(prompt, max_tokens=160, timeout=20):
    api=os.getenv("GROQ_API_KEY","")
    if not api: raise RuntimeError("GROQ_API_KEY not set")
    url="https://api.groq.com/openai/v1/chat/completions"
    model=os.getenv("GROQ_MODEL","llama-3.2-1b-preview")
    r=requests.post(url,headers={"Authorization":f"Bearer {api}"},
                    json={"model":model,"messages":[{"role":"user","content":prompt}],
                          "max_tokens":max_tokens,"temperature":0.6},timeout=timeout)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()


def _ollama_up(url: str) -> bool:
    try:
        import requests
        r = requests.get(url.rstrip("/") + "/api/tags", timeout=1.5)
        return r.ok
    except Exception:
        pass
        
    # Prefer Ollama when reachable and NOT disabled via env
    try:
        import os
        ollama_disabled = str(os.getenv("OLLAMA_DISABLED","0")).strip() in ("1","true","True")
        ollama_url = os.getenv("OLLAMA_URL","http://127.0.0.1:11434")
        if not ollama_disabled and _ollama_up(ollama_url):
            print("LLM provider: OLLAMA (", ollama_url, ")", sep="")
            return ollama
    except Exception as _e:
        print("ollama probe failed:", _e)
    print("LLM provider:", False)
    return False

def use_provider():
    """
    Choose LLM provider. Prefers local Ollama when reachable and not disabled via env.
    Falls back to groq -> openai -> anthropic -> ollama (last resort if imports exist).
    """
    import os
    try:
        ollama_disabled = str(os.getenv("OLLAMA_DISABLED","0")).strip().lower() in ("1","true","yes")
        ollama_url = os.getenv("OLLAMA_URL","http://127.0.0.1:11434")
        if not ollama_disabled and _ollama_up(ollama_url):
            print(f"LLM provider: OLLAMA ({ollama_url})")
            return ollama
    except Exception as _e:
        print("ollama probe failed:", _e)

    # Ordered fallbacks if available in this module's globals
    for name in ("groq","openai","anthropic","ollama"):
        prov = globals().get(name)
        if prov is not None:
            print("LLM provider:", name.upper())
            return prov

    # Absolute last resort: raise clear error
    raise RuntimeError("No LLM providers available (ollama/groq/openai/anthropic not importable).")

def use_provider():
    """
    Choose LLM provider. Prefers local Ollama when reachable and not disabled via env.
    Falls back to groq -> openai -> anthropic -> ollama (if available).
    """
    import os
    try:
        ollama_disabled = str(os.getenv("OLLAMA_DISABLED","0")).strip().lower() in ("1","true","yes")
        ollama_url = os.getenv("OLLAMA_URL","http://127.0.0.1:11434")
        if not ollama_disabled and _ollama_up(ollama_url):
            print(f"LLM provider: OLLAMA ({ollama_url})")
            return ollama
    except Exception as _e:
        print("ollama probe failed:", _e)

    for name in ("groq","openai","anthropic","ollama"):
        prov = globals().get(name)
        if prov is not None:
            print("LLM provider:", name.upper())
            return prov

    raise RuntimeError("No LLM providers available (ollama/groq/openai/anthropic not importable).")
