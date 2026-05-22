"""Hybrid Groq/Gemini assistant for the electoral intelligence dashboard."""
from __future__ import annotations

import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from database import DB

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    def load_dotenv(*args, **kwargs):
        return False


ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class AiSettings:
    groq_api_key: str = _env("GROQ_API_KEY", "")
    groq_model: str = _env("GROQ_MODEL", "llama-3.1-8b-instant")
    gemini_api_key: str = _env("GEMINI_API_KEY", "")
    gemini_model: str = _env("GEMINI_MODEL", "gemini-2.5-flash")
    groq_requests_per_minute_per_key: int = _env_int("GROQ_REQUESTS_PER_MINUTE_PER_KEY", 2)
    groq_requests_per_day_per_key: int = _env_int("GROQ_REQUESTS_PER_DAY_PER_KEY", 100)
    gemini_requests_per_minute: int = _env_int("GEMINI_REQUESTS_PER_MINUTE", 8)


SETTINGS = AiSettings()


class GroqKeyManager:
    def __init__(self, api_keys: tuple[str, ...], rpm: int, rpd: int):
        self.api_keys = [key for key in dict.fromkeys(api_keys) if key]
        self.rpm = rpm
        self.rpd = rpd
        self.index = 0
        self.lock = threading.Lock()
        now = time.time()
        self.usage = {
            i: {"minute": 0, "day": 0, "minute_at": now, "day_at": now}
            for i in range(len(self.api_keys))
        }

    def _reset(self) -> None:
        now = time.time()
        for usage in self.usage.values():
            if now - usage["minute_at"] >= 60:
                usage["minute"] = 0
                usage["minute_at"] = now
            if now - usage["day_at"] >= 86400:
                usage["day"] = 0
                usage["day_at"] = now

    def next_key(self) -> tuple[str | None, int]:
        with self.lock:
            self._reset()
            if not self.api_keys:
                return None, -1
            for _ in range(len(self.api_keys)):
                idx = self.index % len(self.api_keys)
                self.index = (idx + 1) % len(self.api_keys)
                usage = self.usage[idx]
                if usage["minute"] < self.rpm and usage["day"] < self.rpd:
                    usage["minute"] += 1
                    usage["day"] += 1
                    return self.api_keys[idx], idx
            return None, -1


class MinuteLimiter:
    def __init__(self, rpm: int):
        self.rpm = rpm
        self.lock = threading.Lock()
        self.count = 0
        self.started = time.time()

    def allow(self) -> bool:
        with self.lock:
            now = time.time()
            if now - self.started >= 60:
                self.count = 0
                self.started = now
            if self.count >= self.rpm:
                return False
            self.count += 1
            return True


GROQ_KEYS = GroqKeyManager(
    (SETTINGS.groq_api_key,),
    SETTINGS.groq_requests_per_minute_per_key,
    SETTINGS.groq_requests_per_day_per_key,
)
GEMINI_LIMITER = MinuteLimiter(SETTINGS.gemini_requests_per_minute)


def build_dashboard_context() -> str:
    stats = DB.query_one(
        """
        SELECT COUNT(*) AS stations,
               SUM(registered_voters_2022) AS registered,
               AVG(turnout_rate_2022) AS turnout,
               SUM(CASE WHEN mobilization_tier='Critical Priority' THEN 1 ELSE 0 END) AS critical,
               SUM(CASE WHEN mobilization_tier='High Priority' THEN 1 ELSE 0 END) AS high_priority_count,
               SUM(untapped_voters) AS untapped
        FROM features
        """
    ) or {}
    top_priority = DB.query_all(
        """
        SELECT polling_station_id, polling_station_name, registered_voters_2022, untapped_voters,
               win_margin_pct_2022, mobilization_tier, mobilization_score,
               result_source_type
        FROM features
        ORDER BY mobilization_score DESC
        LIMIT 8
        """
    )
    winners = DB.query_all(
        "SELECT year, candidate_name, party_abbrev, votes, source_type FROM historical_winners ORDER BY year"
    )
    sentiment = DB.query_all(
        """
        SELECT candidate_name, AVG(sentiment_score) AS sentiment_score,
               SUM(total_mentions) AS total_mentions, MAX(source_type) AS source_type
        FROM sentiment_data
        WHERE candidate_name = 'Hon. Silverster Ogina'
        GROUP BY candidate_name
        ORDER BY total_mentions DESC
        LIMIT 8
        """
    )
    issues = DB.query_all(
        """
        SELECT primary_theme, SUM(total_mentions) AS total_mentions,
               AVG(sentiment_score) AS sentiment_score, MAX(source_type) AS source_type
        FROM sentiment_data
        WHERE primary_theme IS NOT NULL AND primary_theme != ''
          AND candidate_name = 'Hon. Silverster Ogina'
        GROUP BY primary_theme
        ORDER BY total_mentions DESC
        LIMIT 8
        """
    )
    sources = DB.query_all("SELECT source_key, source_type, description FROM data_sources ORDER BY source_key")

    return (
        "Campaign principal: Incoming MCA Hon. Silverster Ogina.\n"
        "Audience: answer directly to Hon. Silverster Ogina using second person: you, your, your campaign, your field team.\n"
        "Candidate rule: Hon. Silverster Ogina is the client and campaign beneficiary. Other candidates are opponents or historical context; do not recommend promoting or leveraging them unless the user explicitly asks about coalition strategy.\n"
        "Geographic scope: Embakasi Ward only, Nairobi County. This dashboard is not for the whole Embakasi East constituency.\n"
        "Mobilization ranking basis: current station priorities are based on 2022 turnout/result inputs and 2022 voter registration. Tell the user to use them as a starting ground plan and improve them through admin uploads/field updates.\n"
        "Product purpose: help the MCA understand where to mobilize and how to use the dashboard weekly.\n"
        "Sentiment context: sentiment and issue signals can come from online public discussion, field notes, barazas, door-to-door reports, and direct resident interactions in Embakasi. Do not describe the sentiment source as only social media.\n"
        "Planning rule: use every currently loaded row as operational planning input. Source type is metadata only; do not refuse to rank stations because rows are placeholders.\n\n"
        f"Core stats: polling stations={stats.get('stations', 0)}, registered voters={stats.get('registered', 0)}, "
        f"average turnout={round((stats.get('turnout') or 0) * 100, 1)}%, "
        f"critical stations={stats.get('critical', 0)}, high-priority stations={stats.get('high_priority_count', 0)}, "
        f"untapped voters={stats.get('untapped', 0)}.\n\n"
        "Historical winners:\n"
        + "\n".join(f"- {w['year']}: {w['candidate_name']} ({w['party_abbrev']}), {w['votes']} votes, source={w['source_type']}" for w in winners)
        + "\n\nTop priority stations:\n"
        + "\n".join(
            f"- {r['polling_station_name']} ({r['polling_station_id']}): tier={r['mobilization_tier']}, score={r['mobilization_score']}, "
            f"registered={r['registered_voters_2022']}, untapped={r['untapped_voters']}, "
            f"margin={round((r['win_margin_pct_2022'] or 0) * 100, 1)}%, result_source={r['result_source_type']}"
            for r in top_priority
        )
        + "\n\nSentiment summary:\n"
        + "\n".join(
            f"- {r['candidate_name']}: sentiment={round(r['sentiment_score'] or 0, 2)}, mentions={r['total_mentions']}, source={r['source_type']}"
            for r in sentiment
        )
        + "\n\nEmbakasi Ward resident issue signals:\n"
        + "\n".join(
            f"- {r['primary_theme']}: sentiment={round(r['sentiment_score'] or 0, 2)}, mentions={r['total_mentions']}, source={r['source_type']}"
            for r in issues
        )
        + "\n\nSource metadata for internal use only:\n"
        + "\n".join(f"- {s['source_key']}: {s['source_type']} - {s['description']}" for s in sources)
    )


def is_deep_question(question: str) -> bool:
    q = question.lower()
    deep_terms = [
        "strategy", "plan", "weekly", "what should", "how should", "prioritize",
        "explain", "pitch", "speech", "mobilization", "opponent", "sentiment",
        "compare", "roadmap", "campaign"
    ]
    return len(question.split()) > 28 or any(term in q for term in deep_terms)


def local_fallback_answer(question: str, context: str, providers_tried: list[str] | None = None) -> str:
    reasons = providers_tried or []
    if not SETTINGS.groq_api_key and not SETTINGS.gemini_api_key:
        opening = "AI keys are not configured yet, so this is the built-in guidance."
    elif reasons:
        opening = "The AI providers could not answer this request, so this is the built-in guidance."
    else:
        opening = "The built-in guidance is being used for this request."
    reason_text = ""
    if reasons:
        cleaned = "; ".join(reason.split(":", 1)[0] for reason in reasons)
        reason_text = f"\n\nProvider status: {cleaned}."
    stats = DB.query_one(
        """
        SELECT COUNT(*) AS stations,
               SUM(CASE WHEN mobilization_tier='Critical Priority' THEN 1 ELSE 0 END) AS critical,
               SUM(CASE WHEN mobilization_tier='High Priority' THEN 1 ELSE 0 END) AS high_priority_count,
               SUM(untapped_voters) AS untapped
        FROM features
        """
    ) or {}
    priority = DB.query_all(
        """
        SELECT polling_station_id, polling_station_name, registered_voters_2022, untapped_voters,
               win_margin_pct_2022, mobilization_tier, mobilization_score
        FROM features
        ORDER BY mobilization_score DESC
        LIMIT 8
        """
    )
    station_lines = "\n".join(
        f"- {r['polling_station_name']} ({r['polling_station_id']}): {r['mobilization_tier']}, "
        f"score {r['mobilization_score']}, {r['registered_voters_2022']} registered, "
        f"{r['untapped_voters']} untapped, margin {round((r['win_margin_pct_2022'] or 0) * 100, 1)}%."
        for r in priority
    )
    return (
        f"{opening}{reason_text}\n\n"
        "**Stations to Prioritize Now**\n"
        f"Use the current dashboard ranking. It shows {stats.get('critical', 0)} Critical Priority stations, "
        f"{stats.get('high_priority_count', 0)} High Priority stations, and {stats.get('untapped', 0)} untapped voters across Embakasi.\n\n"
        f"{station_lines}\n\n"
        "**Field Action**\n"
        "1. Send canvassers first to the top-ranked Critical Priority stations.\n"
        "2. Use the untapped-voter count to decide how many volunteers each station gets.\n"
        "3. Record resident issues and supporter counts after every visit so replacement real data improves the next ranking.\n"
        "4. Keep High Priority stations on a weekly rotation after the Critical stations are covered."
    )


def groq_answer(prompt: str) -> tuple[str | None, str]:
    api_key, key_index = GROQ_KEYS.next_key()
    if not api_key:
        return None, "groq_rate_limited_or_missing"
    try:
        from groq import Groq

        client = Groq(api_key=api_key)
        response = client.chat.completions.create(
            model=SETTINGS.groq_model,
            messages=[
                {"role": "system", "content": "You are a concise campaign intelligence analyst advising Hon. Silverster Ogina directly. Address him as you/your. Other candidates are opponents or context, not people to promote."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.35,
            max_tokens=850,
        )
        content = response.choices[0].message.content.strip() if response.choices else ""
        return content or None, f"groq:{SETTINGS.groq_model}:key_{key_index + 1}"
    except Exception as exc:
        return None, f"groq_failed:{exc}"


def gemini_answer(prompt: str) -> tuple[str | None, str]:
    if not SETTINGS.gemini_api_key:
        return None, "gemini_missing"
    if not GEMINI_LIMITER.allow():
        return None, "gemini_rate_limited"
    try:
        import google.generativeai as genai

        genai.configure(api_key=SETTINGS.gemini_api_key)
        model = genai.GenerativeModel(SETTINGS.gemini_model)
        response = model.generate_content(prompt)
        content = (response.text or "").strip()
        return content or None, f"gemini:{SETTINGS.gemini_model}"
    except Exception as exc:
        return None, f"gemini_failed:{exc}"


def sanitize_campaign_answer(answer: str) -> str:
    replacements = [
        (
            r"that leverag(?:e|es|ing) the positive sentiment around Hon\.?\s*Silverster Ogina and Nyantika Ricardo Billy",
            "that uses your positive sentiment to strengthen your campaign, while treating Nyantika Ricardo Billy as the opponent benchmark",
        ),
        (
            r"that leverag(?:e|es|ing) positive sentiment around Hon\.?\s*Silverster Ogina and Nyantika Ricardo Billy",
            "that uses your positive sentiment to strengthen your campaign, while treating Nyantika Ricardo Billy as the opponent benchmark",
        ),
        (
            r"leverag(?:e|es|ing) the positive sentiment around Hon\.?\s*Silverster Ogina and Nyantika Ricardo Billy",
            "use your positive sentiment to strengthen your campaign, while treating Nyantika Ricardo Billy as the opponent benchmark",
        ),
        (
            r"leverag(?:e|es|ing) positive sentiment around Hon\.?\s*Silverster Ogina and Nyantika Ricardo Billy",
            "use your positive sentiment to strengthen your campaign, while treating Nyantika Ricardo Billy as the opponent benchmark",
        ),
        (
            r"leverag(?:e|es|ing) the positive sentiment around Nyantika Ricardo Billy",
            "study Nyantika Ricardo Billy's sentiment as an opponent signal",
        ),
        (
            r"leverag(?:e|es|ing) positive sentiment around Nyantika Ricardo Billy",
            "study Nyantika Ricardo Billy's sentiment as an opponent signal",
        ),
    ]
    cleaned = answer
    for pattern, replacement in replacements:
        cleaned = re.sub(pattern, replacement, cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bEmbakasi East constituency\b", "Embakasi Ward", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bEmbakasi East\b", "Embakasi Ward", cleaned, flags=re.IGNORECASE)
    return cleaned


def answer_question(question: str) -> dict:
    question = re.sub(r"\s+", " ", question or "").strip()
    if not question:
        return {"answer": "Ask a question about the dashboard, mobilization, data gaps, or campaign strategy.", "provider": "none"}

    context = build_dashboard_context()
    prompt = (
        "Use only the dashboard context below. Be practical and direct. "
        "Address Hon. Silverster Ogina directly in second person using 'you' and 'your'. "
        "Do not speak about Hon. Silverster Ogina in third person unless identifying him once. "
        "Treat Nyantika Ricardo Billy and all other named candidates as opponents or historical context, not campaign beneficiaries. "
        "Do not recommend leveraging, boosting, or promoting any opponent's sentiment. Instead explain what you should do to improve your own position against them. "
        "Use all currently loaded data for planning, including placeholder/demo rows. "
        "Do not frame the answer as constituency-wide Embakasi East analysis; keep every recommendation specific to Embakasi Ward. "
        "Do not say you cannot prioritize because data is placeholder. "
        "Do not include a Data Source Notes, caveats, limitations, or placeholder-data disclaimer section. "
        "When advising the MCA, give concrete campaign actions. "
        "Format with Markdown headings, bullets, and numbered lists. Put every bullet or numbered item on its own line.\n\n"
        f"DASHBOARD CONTEXT:\n{context}\n\n"
        f"QUESTION:\n{question}\n\n"
        "ANSWER FORMAT: short paragraphs, bullets, or numbered lists, no hype, no invented facts."
    )

    providers_tried: list[str] = []
    if is_deep_question(question):
        answer, provider = gemini_answer(prompt)
        providers_tried.append(provider)
        if answer:
            return {"answer": sanitize_campaign_answer(answer), "provider": provider, "providers_tried": providers_tried}
        answer, provider = groq_answer(prompt)
        providers_tried.append(provider)
        if answer:
            return {"answer": sanitize_campaign_answer(answer), "provider": provider, "providers_tried": providers_tried}
    else:
        answer, provider = groq_answer(prompt)
        providers_tried.append(provider)
        if answer:
            return {"answer": sanitize_campaign_answer(answer), "provider": provider, "providers_tried": providers_tried}
        answer, provider = gemini_answer(prompt)
        providers_tried.append(provider)
        if answer:
            return {"answer": sanitize_campaign_answer(answer), "provider": provider, "providers_tried": providers_tried}

    return {"answer": sanitize_campaign_answer(local_fallback_answer(question, context, providers_tried)), "provider": "local_fallback", "providers_tried": providers_tried}


def ai_status() -> dict:
    statuses: dict[str, object] = {
        "groq_key_loaded": bool(SETTINGS.groq_api_key),
        "gemini_key_loaded": bool(SETTINGS.gemini_api_key),
        "groq_model": SETTINGS.groq_model,
        "gemini_model": SETTINGS.gemini_model,
    }
    try:
        import groq  # noqa: F401
        statuses["groq_package"] = True
    except Exception as exc:
        statuses["groq_package"] = False
        statuses["groq_package_error"] = type(exc).__name__
    try:
        import google.generativeai  # noqa: F401
        statuses["gemini_package"] = True
    except Exception as exc:
        statuses["gemini_package"] = False
        statuses["gemini_package_error"] = type(exc).__name__
    return statuses
