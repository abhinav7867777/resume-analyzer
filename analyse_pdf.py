# ============================================================
# analyse_pdf.py
# OpenRouter API — Multi-model fallback chain
# Returns structured JSON: score, skills, suggestions, summary
# + rewrite_resume_section() for the AI editor feature
# ============================================================

import requests
import os
import json
import re
from dotenv import load_dotenv

load_dotenv()

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# ── Fallback chain (10 models, 6 different providers) ──
MODELS = [
    "meta-llama/llama-3.3-70b-instruct:free",
    "google/gemma-3-27b-it:free",
    "nvidia/nemotron-super-49b-v1:free",
    "qwen/qwen3-30b-a3b:free",
    "mistralai/mistral-7b-instruct:free",
    "openai/gpt-4o-mini-search-preview:free",
    "nvidia/nemotron-nano-9b-v2:free",
    "meta-llama/llama-3.2-3b-instruct:free",
    "microsoft/phi-4-reasoning-plus:free",
    "nousresearch/hermes-3-llama-3.1-405b:free",
]


def _call_api(messages, max_tokens=2000):
    """Internal: tries each model in fallback chain. Returns (text, error)."""
    if not OPENROUTER_API_KEY:
        return None, "OPENROUTER_API_KEY not set in .env file."

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "http://localhost:5000",
        "X-Title": "Resume Analyzer",
    }

    last_error = "All models exhausted."

    for model in MODELS:
        body = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0.5,
        }
        try:
            response = requests.post(
                OPENROUTER_URL, headers=headers, json=body, timeout=60
            )

            if response.status_code == 401:
                return None, "API Key is invalid. Check OPENROUTER_API_KEY in .env."

            if response.status_code in (429, 503):
                last_error = f"Model '{model}' rate limited, trying next..."
                continue

            if not response.ok:
                err = response.json().get("error", {}).get(
                    "message", f"HTTP {response.status_code}"
                )
                last_error = f"Model '{model}' failed: {err}"
                continue

            data = response.json()
            text = data["choices"][0]["message"]["content"]
            return text, None

        except requests.exceptions.ConnectionError:
            return None, "No internet connection or OpenRouter is down."
        except requests.exceptions.Timeout:
            last_error = f"Model '{model}' timed out, trying next..."
            continue
        except Exception as e:
            return None, f"Unexpected error: {str(e)}"

    return None, last_error


def analyse_resume_gemini(resume_content, job_description):
    """
    Analyze resume against JD. Returns a dict with:
      match_score, grade, missing_skills, suggestions, summary, raw_text
    On error returns a dict with 'error' key.
    """

    prompt = f"""You are an expert resume analyzer and career coach.

RESUME:
{resume_content}

JOB DESCRIPTION:
{job_description}

Analyze the resume against the job description and respond with ONLY a valid JSON object in this exact format (no markdown, no extra text):
{{
  "match_score": <integer 0-100>,
  "grade": "<Excellent Match | Good Match | Average Match | Low Match>",
  "missing_skills": [
    {{"skill": "<skill name>", "priority": "<critical|important|nice-to-have>", "reason": "<why it matters>"}}
  ],
  "suggestions": [
    {{"id": 1, "category": "<Skills|Experience|Summary|Format|Keywords>", "suggestion": "<specific actionable suggestion>", "example": "<short example text to add>"}}
  ],
  "summary": "<2-3 sentence overall assessment>",
  "strengths": ["<strength 1>", "<strength 2>", "<strength 3>"]
}}

Rules:
- match_score must be a number between 0 and 100
- missing_skills must have 3-8 items
- suggestions must have 4-8 items with specific, actionable advice
- Return ONLY the JSON, no markdown code blocks, no extra explanation"""

    messages = [
        {
            "role": "system",
            "content": "You are an expert resume analyzer. Always respond with valid JSON only, no markdown.",
        },
        {"role": "user", "content": prompt},
    ]

    text, error = _call_api(messages, max_tokens=2500)

    if error:
        return {"error": error}

    # Try to parse JSON from the response
    try:
        # Strip any accidental markdown fences
        cleaned = re.sub(r"```json|```", "", text).strip()
        data = json.loads(cleaned)
        data["raw_text"] = text
        return data
    except json.JSONDecodeError:
        # Fallback: return raw text so UI can still display something
        return {
            "error": None,
            "match_score": 0,
            "grade": "Unknown",
            "missing_skills": [],
            "suggestions": [],
            "summary": text,
            "strengths": [],
            "raw_text": text,
        }


def rewrite_resume_section(resume_text, suggestions, section="full"):
    """
    AI rewrites the resume (or a specific section) using the suggestions.
    Returns the improved resume text string, or an error string.
    section: 'full' | 'summary' | 'skills' | 'experience'
    """

    if section == "full":
        instruction = "Rewrite the ENTIRE resume incorporating all the suggestions. Keep the same structure but improve every section."
    else:
        instruction = f"Rewrite only the '{section}' section of the resume incorporating the suggestions. Return only that section."

    suggestion_text = "\n".join(
        [f"- {s.get('suggestion', s) if isinstance(s, dict) else s}" for s in suggestions]
    )

    prompt = f"""You are a professional resume writer.

ORIGINAL RESUME:
{resume_text}

IMPROVEMENT SUGGESTIONS:
{suggestion_text}

TASK: {instruction}
- Keep all factual information (dates, companies, education) intact
- Improve phrasing, add missing keywords, strengthen bullet points
- Make it ATS-friendly (Applicant Tracking System)
- Use strong action verbs
- Keep formatting clean with clear sections

Return ONLY the improved resume text, no explanations."""

    messages = [
        {
            "role": "system",
            "content": "You are a professional resume writer. Return only the improved resume text.",
        },
        {"role": "user", "content": prompt},
    ]

    text, error = _call_api(messages, max_tokens=3000)

    if error:
        return f"Error: {error}"

    return text
