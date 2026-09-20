"""
llm/client.py
=============

HireFlow ka central LLM gateway.

    core/  -->  llm/client.py  -->  Groq API  -->  llm/client.py  -->  structured result  -->  core/

Is file ka poora purpose: project ke kisi bhi aur part ko Groq SDK se
directly deal na karna pade. Yahan hai:

    - Groq client initialization (API key .env se)
    - Ek common gateway function: `generate(...)`
    - Prompt file loading (prompts/*.txt), prompt content yahan nahi likha
    - Structured JSON -> Pydantic validation
    - Retry with backoff (tenacity), sirf transient errors par
    - Centralized, safe error handling (koi raw SDK traceback leak nahi)
    - Timeout / request safety
    - Basic, privacy-safe logging (operation name, duration, status --
      candidate/resume/JD content kabhi log nahi)

Yahan NAHI hai (jaanbujh kar):
    - Business logic (MET/PARTIAL decide karna)      -> core/
    - PDF/resume parsing                              -> ingestion.py
    - DB operations                                   -> db/
    - Streamlit UI                                    -> pages/
    - Hardcoded prompts                               -> prompts/*.txt
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Type, TypeVar

from dotenv import load_dotenv
from groq import Groq
from groq import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    RateLimitError,
)
from pydantic import BaseModel, ValidationError
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

# ---------------------------------------------------------------------------
# Config / environment
# ---------------------------------------------------------------------------

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
GROQ_TIMEOUT_SECONDS = float(os.getenv("GROQ_TIMEOUT_SECONDS", "30"))
GROQ_MAX_RETRIES = int(os.getenv("GROQ_MAX_RETRIES", "3"))
GROQ_TEMPERATURE = float(os.getenv("GROQ_TEMPERATURE", "0.2"))
GROQ_MAX_TOKENS = int(os.getenv("GROQ_MAX_TOKENS", "2048"))

PROMPTS_DIR = Path(__file__).parent / "prompts"

logger = logging.getLogger("hireflow.llm")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
    )
    logger.addHandler(handler)
logger.setLevel(logging.INFO)


# ---------------------------------------------------------------------------
# Errors — application-level, no raw SDK/secret leakage
# ---------------------------------------------------------------------------

class LLMError(Exception):
    """Base class for all client.py errors. Core layer sirf isse catch kare."""


class LLMConfigError(LLMError):
    """Missing/invalid configuration (e.g. API key not set)."""


class LLMAuthError(LLMError):
    """Authentication failed. Retry karne se koi fayda nahi."""


class LLMRateLimitError(LLMError):
    """Rate limit hit — retries already exhaust ho chuke hain."""


class LLMTimeoutError(LLMError):
    """Request timeout ho gaya."""


class LLMResponseError(LLMError):
    """LLM ne malformed / unparseable output diya."""


class LLMValidationError(LLMError):
    """LLM output diya, but Pydantic schema ke against validate nahi hua."""

    def __init__(self, message: str, raw_output: str):
        super().__init__(message)
        self.raw_output = raw_output


# ---------------------------------------------------------------------------
# Client initialization (singleton)
# ---------------------------------------------------------------------------

_client: Groq | None = None


def _get_client() -> Groq:
    global _client
    if _client is None:
        if not GROQ_API_KEY:
            raise LLMConfigError(
                "GROQ_API_KEY not set. Add it to your .env file."
            )
        _client = Groq(api_key=GROQ_API_KEY, timeout=GROQ_TIMEOUT_SECONDS)
    return _client


# ---------------------------------------------------------------------------
# Prompt loading (prompts/*.txt) — content yahan hardcode nahi
# ---------------------------------------------------------------------------

def load_prompt(prompt_name: str, **kwargs: str) -> str:
    """
    prompts/<prompt_name>.txt load karta hai aur optional {placeholders} ko
    kwargs se fill karta hai.

    Example:
        load_prompt("extract_resume", resume_text=resume_text)
    """
    prompt_path = PROMPTS_DIR / f"{prompt_name}.txt"

    if not prompt_path.exists():
        raise LLMConfigError(
            f"Prompt file not found: {prompt_path}"
        )

    template = prompt_path.read_text(encoding="utf-8")

    try:
        return template.format(**kwargs) if kwargs else template
    except KeyError as exc:
        raise LLMConfigError(
            f"Prompt '{prompt_name}' expects placeholder "
            f"{exc} that wasn't provided."
        ) from exc

# ---------------------------------------------------------------------------
# Retry policy — sirf transient errors par retry, auth/validation/4xx par nahi
# ---------------------------------------------------------------------------

def _is_retryable(exc: BaseException) -> bool:
    """
    Retry only transient failures:
      - connection errors, timeouts, 429 rate limits
      - 5xx server errors
    Auth (401) and other 4xx (bad model, bad request) fail immediately.
    """
    if isinstance(exc, (APIConnectionError, APITimeoutError, RateLimitError)):
        return True
    if isinstance(exc, APIStatusError):
        return exc.status_code >= 500
    return False


def _log_retry(retry_state) -> None:
    logger.warning(
        "LLM call retrying | attempt=%s | wait=%.1fs",
        retry_state.attempt_number,
        retry_state.next_action.sleep if retry_state.next_action else 0,
    )


_retry_decorator = retry(
    retry=retry_if_exception(_is_retryable),
    stop=stop_after_attempt(GROQ_MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    before_sleep=_log_retry,
    reraise=True,
)


# ---------------------------------------------------------------------------
# Core raw call (private) — retryable, but no schema knowledge
# ---------------------------------------------------------------------------

@_retry_decorator
def _call_groq(prompt: str, operation: str) -> str:
    client = _get_client()
    response = client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=GROQ_TEMPERATURE,
        max_tokens=GROQ_MAX_TOKENS,
    )
    content = response.choices[0].message.content
    if not content or not content.strip():
        raise LLMResponseError(f"Empty response from LLM (operation={operation})")
    return content


# ---------------------------------------------------------------------------
# JSON extraction helper — LLM kabhi ```json fences ya extra text de deta hai
# ---------------------------------------------------------------------------

def _extract_json(raw: str) -> dict | list:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
    cleaned = cleaned.strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Fallback: pehla { ... } ya [ ... ] block dhoondo (agar LLM ne
    # preamble text add kar diya ho)
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start = cleaned.find(open_ch)
        end = cleaned.rfind(close_ch)
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(cleaned[start : end + 1])
            except json.JSONDecodeError:
                continue

    raise LLMResponseError("Could not parse JSON from LLM response")


# ---------------------------------------------------------------------------
# PUBLIC API — is file ka baaki project kewal in functions ko use karega
# ---------------------------------------------------------------------------

T = TypeVar("T", bound=BaseModel)


def ask_llm(prompt: str, operation: str = "generic") -> str:
    """
    Sabse simple gateway: raw text prompt -> raw text response.

    Structured/validated output chahiye ho to `generate()` use karo.
    """
    start = time.monotonic()
    logger.info("LLM request started | operation=%s | model=%s", operation, GROQ_MODEL)

    try:
        result = _call_groq(prompt, operation)
        duration = time.monotonic() - start
        logger.info(
            "LLM request succeeded | operation=%s | duration=%.2fs", operation, duration
        )
        return result

    except AuthenticationError as exc:
        logger.error("LLM auth failed | operation=%s", operation)
        raise LLMAuthError("LLM authentication failed. Check API key.") from exc

    except RateLimitError as exc:
        logger.error("LLM rate limit exhausted | operation=%s", operation)
        raise LLMRateLimitError(
            "LLM service is rate-limited right now. Try again shortly."
        ) from exc

    except APITimeoutError as exc:
        logger.error("LLM timeout | operation=%s", operation)
        raise LLMTimeoutError("LLM request timed out.") from exc

    except (APIConnectionError, APIStatusError) as exc:
        logger.error(
            "LLM service error | operation=%s | error=%s | status=%s",
            operation,
            type(exc).__name__,
            getattr(exc, "status_code", None),
        )
        raise LLMError("LLM service temporarily unavailable.") from exc

    except LLMError:
        raise

    except Exception as exc:  # noqa: BLE001 — last-resort safety net
        logger.error(
            "LLM unexpected error | operation=%s | error_type=%s", operation, type(exc).__name__
        )
        raise LLMError("Unexpected error while calling LLM.") from exc


def generate(prompt: str, schema: Type[T], operation: str = "generic") -> T:
    """
    Structured gateway: prompt -> validated Pydantic object.

        Groq response -> JSON parse -> Pydantic validation -> T

    schema: koi bhi schemas.py mein defined Pydantic model
            (CandidateProfile, Mapping, InterviewReport, ...)

    Raises:
        LLMResponseError    -- JSON parse hi nahi hua
        LLMValidationError  -- JSON mila, but schema ke against invalid
        LLMAuthError / LLMRateLimitError / LLMTimeoutError / LLMError
    """
    raw_output = ask_llm(prompt, operation=operation)

    parsed = _extract_json(raw_output)

    try:
        return schema.model_validate(parsed)
    except ValidationError as exc:
        logger.error(
            "LLM response failed schema validation | operation=%s | schema=%s",
            operation,
            schema.__name__,
        )
        raise LLMValidationError(
            f"LLM output did not match expected schema '{schema.__name__}'.",
            raw_output=raw_output,
        ) from exc


def generate_from_prompt_file(
    prompt_name: str,
    schema: Type[T],
    operation: str | None = None,
    **prompt_kwargs: str,
) -> T:
    """
    Convenience wrapper: prompts/<prompt_name>.txt load karke fill karo,
    Groq ko bhejo, aur `schema` ke against validate karke return karo.

    Example:
        profile = generate_from_prompt_file(
            "extract_resume",
            schema=CandidateProfile,
            operation="resume_extraction",
            resume_text=resume_text,
        )
    """
    prompt = load_prompt(prompt_name, **prompt_kwargs)
    return generate(prompt, schema=schema, operation=operation or prompt_name)
