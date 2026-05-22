"""Sentiment scoring for uploaded resident notes and public discussion rows."""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    def load_dotenv(*args, **kwargs):
        return False


ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

ISSUE_KEYWORDS = {
    "Water Supply": ["water", "maji", "shortage", "ration", "tap", "borehole"],
    "Roads & Drainage": ["road", "roads", "barabara", "drain", "drainage", "flood", "mud", "pothole"],
    "Security": ["security", "crime", "robbery", "thief", "wezi", "lighting", "lights", "unsafe"],
    "Youth Jobs": ["job", "jobs", "youth", "vijana", "kazi", "training", "sports"],
    "Waste Collection": ["waste", "garbage", "trash", "taka", "dump", "collection", "smell"],
    "Bursaries & School Fees": ["bursary", "school fees", "fees", "scholarship", "students", "wanafunzi"],
    "Health Services": ["health", "clinic", "hospital", "medicine", "dawa", "maternal", "sick"],
    "Business Permits": ["permit", "business", "trader", "market", "kiosk", "license", "hawker"],
}

SWAHILI_MARKERS = {
    "maji", "barabara", "usalama", "vijana", "kazi", "taka", "dawa", "soko",
    "mtaa", "wananchi", "shule", "ada", "bursary", "tafadhali", "serikali",
    "mca", "kura", "watu", "nyumba", "matatu", "boda",
}

SWAHILI_POSITIVE = {"mzuri", "vizuri", "asante", "tunapenda", "support", "saidia", "bora", "sawa"}
SWAHILI_NEGATIVE = {"mbaya", "shida", "hatuna", "hakuna", "ghali", "chafu", "wizi", "hatari", "kero"}


@dataclass
class SentimentResult:
    sentiment_score: float
    primary_theme: str
    language: str
    sentiment_method: str


def detect_language(text: str) -> str:
    words = set(re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ']+", text.lower()))
    if words & SWAHILI_MARKERS:
        return "swahili"
    return "english"


def detect_issue_theme(text: str) -> str:
    lowered = text.lower()
    best_theme = "General Campaign Feedback"
    best_count = 0
    for theme, keywords in ISSUE_KEYWORDS.items():
        count = sum(1 for keyword in keywords if keyword in lowered)
        if count > best_count:
            best_theme = theme
            best_count = count
    return best_theme


def clamp_score(value: float) -> float:
    return max(-1.0, min(1.0, round(float(value), 3)))


def english_vader_score(text: str) -> float:
    from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

    analyzer = SentimentIntensityAnalyzer()
    return clamp_score(analyzer.polarity_scores(text)["compound"])


def local_swahili_score(text: str) -> float:
    words = set(re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ']+", text.lower()))
    positives = len(words & SWAHILI_POSITIVE)
    negatives = len(words & SWAHILI_NEGATIVE)
    if positives == negatives:
        return 0.0
    return clamp_score((positives - negatives) / max(positives + negatives, 1))


def parse_json_object(raw: str) -> dict:
    match = re.search(r"\{.*\}", raw or "", flags=re.S)
    if not match:
        return {}
    try:
        return json.loads(match.group(0))
    except Exception:
        return {}


def ai_classify_swahili(text: str) -> SentimentResult | None:
    prompt = (
        "Classify this Embakasi Ward resident feedback. Return JSON only with keys "
        "sentiment_score between -1 and 1, primary_theme, and language. "
        "Allowed primary_theme values: Water Supply, Roads & Drainage, Security, Youth Jobs, "
        "Waste Collection, Bursaries & School Fees, Health Services, Business Permits, General Campaign Feedback.\n\n"
        f"TEXT: {text[:2000]}"
    )
    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    if gemini_key:
        try:
            import google.generativeai as genai

            genai.configure(api_key=gemini_key)
            model = genai.GenerativeModel(os.getenv("GEMINI_MODEL", "gemini-2.5-flash"))
            data = parse_json_object(model.generate_content(prompt).text or "")
            if data:
                return SentimentResult(
                    sentiment_score=clamp_score(data.get("sentiment_score", 0)),
                    primary_theme=str(data.get("primary_theme") or detect_issue_theme(text)),
                    language=str(data.get("language") or "swahili"),
                    sentiment_method="gemini_swahili",
                )
        except Exception:
            pass

    groq_key = os.getenv("GROQ_API_KEY", "").strip()
    if groq_key:
        try:
            from groq import Groq

            client = Groq(api_key=groq_key)
            response = client.chat.completions.create(
                model=os.getenv("GROQ_MODEL", "llama-3.1-8b-instant"),
                messages=[
                    {"role": "system", "content": "Return valid JSON only."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                max_tokens=180,
            )
            content = response.choices[0].message.content if response.choices else ""
            data = parse_json_object(content)
            if data:
                return SentimentResult(
                    sentiment_score=clamp_score(data.get("sentiment_score", 0)),
                    primary_theme=str(data.get("primary_theme") or detect_issue_theme(text)),
                    language=str(data.get("language") or "swahili"),
                    sentiment_method="groq_swahili",
                )
        except Exception:
            pass
    return None


def classify_text(text: str) -> SentimentResult:
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    if not cleaned:
        return SentimentResult(0.0, "General Campaign Feedback", "unknown", "empty")

    language = detect_language(cleaned)
    if language == "english":
        try:
            return SentimentResult(
                sentiment_score=english_vader_score(cleaned),
                primary_theme=detect_issue_theme(cleaned),
                language="english",
                sentiment_method="vader_english",
            )
        except Exception:
            return SentimentResult(0.0, detect_issue_theme(cleaned), "english", "vader_unavailable")

    ai_result = ai_classify_swahili(cleaned)
    if ai_result:
        return ai_result
    return SentimentResult(
        sentiment_score=local_swahili_score(cleaned),
        primary_theme=detect_issue_theme(cleaned),
        language="swahili",
        sentiment_method="local_swahili_fallback",
    )


def polarity_counts(score: float, mentions: int = 1) -> tuple[int, int, int]:
    mentions = max(int(mentions or 1), 1)
    if score >= 0.15:
        return mentions, 0, 0
    if score <= -0.15:
        return 0, 0, mentions
    return 0, mentions, 0
