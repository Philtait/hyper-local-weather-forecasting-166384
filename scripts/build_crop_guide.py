"""Build-time crop guide for the Imarika site, written by Gemini from fixed facts.

    venv\\Scripts\\python.exe scripts\\build_crop_guide.py [--dry-run | --templates-only] [--force]

For each station with a forecast in deploy_data/, the script turns the forecast
into buckets IN CODE (rain label, whole-number rain and temperature, a "same
every day" flag, the season). The model never sees station IDs. For each crop
and language (sw, en) it asks Gemini for exactly three sentences in JSON:
this week's weather, the season (citing a season fact) and one crop note
(citing an allowed crop fact, or advice to ask the agricultural extension
officer). Each answer is validated (JSON, three sentences, allowed fact IDs,
only the provided numbers, no banned words, no other crop names); a failure
is retried once, then replaced by a template. Nothing in the text says it is
AI-generated; the "source" field ("ai" or "template") is kept for our records.

Facts come from docs/busia_facts.md (--facts to change).
The key is read from GEMINI_API_KEY (.env via python-dotenv), sent only in the
x-goog-api-key header, never printed or written. GEMINI_MODEL overrides the
model choice (default: newest text flash-lite model). Responses are cached in
reports/crop_guide_cache/ by a hash of (model, prompt).

Errors: HTTP 503 or a network timeout is retried up to 3 times (waits 10, 30,
60 s); if it still fails, the next model in the chain is tried (default model,
gemini-3.7-flash, gemini-3.8-flash, gemini-2.5-flash; only text models in the
model list). A 404, "no longer available", or a 429 that mentions a quota or
per-day limit skips that model for the rest of the run. Any other 4xx, other
5xx or other network errors stop all calls at once; the rest use templates.
At most 60 HTTP requests per run (retries included), a sleep between calls and
an optional --minutes limit. Each entry records its model; the cache is keyed
by the prompt only and records which model answered.

Numbers: prompts contain labels only (rain and temperature labels, same-every-
day flag, season), so stations with the same labels share one prompt. The
model writes {RAIN} and {TEMP}; the code fills in each station's numbers.
"""

import argparse
import hashlib
import json
import math
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy_data"
FACTS = ROOT / "docs" / "busia_facts.md"
CACHE = ROOT / "reports" / "crop_guide_cache"
OUTPUT = ROOT / "web" / "data" / "crop_guide.json"
API = "https://generativelanguage.googleapis.com/v1beta"
MAX_CALLS = 60
RETRY_WAITS = [10, 30, 60]
FALLBACK_MODELS = ["models/gemini-3.7-flash", "models/gemini-3.8-flash", "models/gemini-2.5-flash"]
# The model writes these tokens; the code fills in each station's rounded numbers.
RAIN_TOKEN, TEMP_TOKEN = "{RAIN}", "{TEMP}"
# Season sentence rules for the end of the short rains (November).
SEASON_PHRASE = {"sw": "mwisho wa msimu wa mvua fupi", "en": "the end of the short rains"}
# Fixed vocabulary: the weather sentence must use the rain phrase for its label.
RAIN_PHRASES = {"en": ["very little or no rain", "light rain", "moderate rain", "heavy rain"],
                "sw": ["mvua kidogo sana au hakuna", "mvua nyepesi", "mvua ya wastani", "mvua kubwa"]}
TEMP_PHRASE = {"sw": "nyuzi joto", "en": "degrees"}
WRONG_VOCABULARY = {"Disemba": "Desemba", "digrii": "nyuzi joto", "mvua ya kiasi": "mvua ya wastani"}
# A sentence citing F09 (May 2023) must say the event was in the past.
PAST_WORDS = {"en": ["past", "earlier"], "sw": ["zamani", "uliopita", "kilichopita"]}
# Units next to the placeholders in the weather sentence.
UNIT_PATTERNS = {"en": [r"\{RAIN\} mm\b", r"\{TEMP\} degrees\b"],
                 "sw": [r"\bmilimita \{RAIN\}", r"\bnyuzi joto \{TEMP\}"]}
# F07 is a finding by ICRISAT, KALRO and the county: name the researchers, not farmers.
F07_SOURCE = ["ICRISAT", "KALRO", "researchers", "watafiti"]
FARMERS_FOUND = (r"\bfarmers?\b[^.;]*\b(found|discovered|learned|learnt)\b"
                 r"|\bwakulima\b[^.;]*\b(waligundua|wamegundua|walibaini|wamebaini|waliona)\b")
NUMBER_WORDS = {
    "en": ["one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
           "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen", "twenty",
           "thirty", "forty", "fifty", "hundred"],
    "sw": ["moja", "mbili", "tatu", "nne", "tano", "sita", "saba", "nane", "tisa", "kumi", "ishirini",
           "thelathini", "arobaini", "hamsini", "mia"],
}
# Advice and over-claiming words: sentence 3 may only restate its fact.
ADVICE_WORDS = {
    "en": ["consider", "should", "substitut", "switch to", "reliable", "studies", "many", "most", "best",
           "always"],
    "sw": ["zingatia", "unapaswa", "badilisha", "tafiti", "utafiti", "nyingi", "bora", "kila mara"],
}
OFFICER_WORDS = ["extension officer", "afisa wa ugani", "afisa wa kilimo"]
LANGUAGES = ["sw", "en"]
CROPS = ["maize", "beans", "sorghum", "cassava", "cowpeas", "sweet potatoes"]
CROP_FACTS = {"maize": ["F01", "F05", "F07", "F09"], "beans": ["F01", "F09"], "sorghum": ["F07"],
              "cassava": ["F01", "F08"], "cowpeas": ["F06", "F07"], "sweet potatoes": ["F01"]}
# Crop names in each language, including crops that facts mention.
CROP_WORDS = {
    "maize": ["maize", "corn", "mahindi"],
    "beans": ["bean", "maharagwe", "maharage"],
    "sorghum": ["sorghum", "mtama"],
    "cassava": ["cassava", "muhogo", "mihogo"],
    "cowpeas": ["cowpea", "kunde"],
    "sweet potatoes": ["sweet potato", "viazi vitamu"],
    "millet": ["millet", "wimbi", "ulezi", "mawele"],
    "sugarcane": ["sugarcane", "sugar cane", "miwa"],
    "pigeon peas": ["pigeon pea", "mbaazi"],
    "green grams": ["green gram", "pojo", "ndengu", "dengu", "choroko"],
}
FACT_CROPS = {"F01": ["sugarcane", "maize", "beans", "sweet potatoes", "millet", "cassava"],
              "F06": ["cowpeas"], "F07": ["maize", "sorghum", "millet", "pigeon peas", "cowpeas", "green grams"],
              "F08": ["cassava", "maize"], "F09": ["maize", "beans"]}
BANNED = {"sw": ["dawa", "kiuatilifu", "viuatilifu", "mbolea", "kipimo", "kunyunyizia"],
          "en": ["pesticide", "fertiliser", "fertilizer", "dose", "spray", "herbicide"]}
CROP_NAMES = {"sw": {"maize": "mahindi", "beans": "maharagwe", "sorghum": "mtama", "cassava": "muhogo",
                     "cowpeas": "kunde", "sweet potatoes": "viazi vitamu"},
              "en": {crop: crop for crop in CROPS}}
RAIN_LABELS = {"en": ["very little or none", "light", "moderate", "heavy"],
               "sw": ["kidogo sana au hakuna", "nyepesi", "ya wastani", "kubwa"]}
TEMP_LABELS = {"en": ["cool", "warm", "hot"], "sw": ["baridi kiasi", "joto", "joto kali"]}


# =============================================================================
# Facts, seasons and buckets
# =============================================================================
def load_facts(path=FACTS):
    """Read 'ID | text | source | verify' lines into {ID: text}."""
    facts = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        match = re.match(r"^(F\d{2})\s*\|\s*(.+?)\s*\|", line)
        if match:
            facts[match.group(1)] = match.group(2)
    return facts


def season(month):
    """Season key, season fact IDs and names for a forecast start month."""
    if month in (8, 9, 10):
        return {"key": "short", "facts": ["F02", "F03", "F04"], "en": "the short rains",
                "sw": "msimu wa mvua fupi"}
    if month == 11:
        return {"key": "short-late", "facts": ["F02", "F03", "F04"], "en": "the end of the short rains",
                "sw": "mwisho wa msimu wa mvua fupi"}
    if month in (12, 1, 2):
        return {"key": "dry", "facts": ["F02", "F03"], "en": "the dry season with scattered rains",
                "sw": "kiangazi chenye mvua za hapa na pale"}
    if month in (3, 4, 5):
        return {"key": "long", "facts": ["F02"], "en": "the long rains", "sw": "msimu wa mvua ndefu"}
    return {"key": "between", "facts": ["F02"], "en": "the time between the long and short rains",
            "sw": "kipindi kati ya mvua ndefu na mvua fupi"}


def whole(value):
    return int(math.floor(float(value) + 0.5))


def rain_index(mm):
    return 0 if mm < 1 else 1 if mm < 5 else 2 if mm < 15 else 3


def temp_index(degrees):
    return 0 if degrees < 25 else 1 if degrees < 32 else 2


def buckets(daily, insights):
    """Per station with a forecast: the bucket the prompt is built from."""
    variation = insights.groupby("device_id").forecast_variation_mm.first() if len(insights) else pd.Series()
    result = {}
    for device_id, days in daily.sort_values("date").groupby("device_id"):
        first = days.iloc[0]
        spread = variation.get(device_id)
        if spread is None or pd.isna(spread):
            spread = days.rain_mm.iloc[:5].max() - days.rain_mm.iloc[:5].min()
        start = pd.Timestamp(first.date)
        result[device_id] = {
            "rain_mm": whole(first.rain_mm), "rain": rain_index(first.rain_mm),
            "temp_max": whole(first.temp_max), "temp": temp_index(whole(first.temp_max)),
            "same_every_day": bool(spread < 1.0), "season": season(start.month)["key"], "month": start.month,
        }
    return result


def allowed_facts(crop, month):
    return sorted(set(CROP_FACTS[crop]) | set(season(month)["facts"]))


# =============================================================================
# Prompt and validation
# =============================================================================
def prompt(bucket, crop, language, facts):
    current = season(bucket["month"])
    crop_name = CROP_NAMES[language][crop]
    fact_lines = "\n".join(f"{fact_id}: {facts[fact_id]}" for fact_id in allowed_facts(crop, bucket["month"]))
    lang = ("simple Kiswahili for a smallholder farmer in Busia" if language == "sw"
            else "plain English for a smallholder farmer in Busia")
    same = "yes" if bucket["same_every_day"] else "no"
    rain_phrase, temp_phrase = RAIN_PHRASES[language][bucket["rain"]], TEMP_PHRASE[language]
    example = (f"{rain_phrase} ya milimita {RAIN_TOKEN} kwa siku na joto la juu la {temp_phrase} {TEMP_TOKEN}"
               if language == "sw" else
               f"{rain_phrase} of about {RAIN_TOKEN} mm a day, with highs near {TEMP_TOKEN} {temp_phrase}")
    vocabulary = ("Use exactly these words: \"Desemba\" (never \"Disemba\"), \"nyuzi joto\" (never \"digrii\"), "
                  "\"mvua ya wastani\" for moderate rain (never \"mvua ya kiasi\"), "
                  "\"mwisho wa msimu wa mvua fupi\" for the end of the short rains."
                  if language == "sw" else
                  "Use exactly these words: \"moderate rain\" for moderate rain and \"end of the short rains\".")
    return (
        f"Write a short weekly crop note in {lang}. Crop: {crop_name}.\n"
        f"Weather this week (from our station forecast): rain {RAIN_LABELS['en'][bucket['rain']]}; highest "
        f"temperature {TEMP_LABELS['en'][bucket['temp']]}; similar every day: {same}.\n"
        f"Season: {current['en']}.\n"
        f"Facts you may cite (use only these IDs):\n{fact_lines}\n\n"
        "Return JSON only: {\"sentences\": [{\"text\": \"...\", \"facts\": [\"...\"]}, ...]} with exactly 3 "
        "sentences.\n"
        f"Sentence 1: this week's weather. Write the rain amount as the literal token {RAIN_TOKEN} and the "
        f"highest temperature as the literal token {TEMP_TOKEN}, exactly as written, for example: "
        f"\"{example}\". Use the phrase \"{rain_phrase}\". If similar every day is yes, say it is similar "
        "every day and do not list days; facts: [\"DATA\"].\n"
        + season_rule(bucket, language) +
        f"Sentence 3: one note about {crop_name} that only restates, in plain words, one fact ID above that is "
        f"about {crop_name}, and cites it. Keep the fact's strength: where a fact says some farmers, write "
        f"{'baadhi ya wakulima' if language == 'sw' else 'some farmers'}. If you cite F09, say the dry spell "
        f"was in the past (use \"{PAST_WORDS[language][0]}\"). No advice and no recommendation. "
        "If no fact fits, say only to ask the agricultural extension officer and cite no fact.\n"
        f"Rules: no digits and no number words anywhere; the only numbers are the tokens {RAIN_TOKEN} and "
        f"{TEMP_TOKEN}; no dates or years. {vocabulary} Never use these words: "
        f"{', '.join(ADVICE_WORDS[language])}. Do not mention pesticides, fertiliser, doses, spraying, "
        f"planting dates or yields. Do not name any crop except {crop_name} and crops named in a fact you cite. "
        "Do not say the text is AI-generated."
    )


def season_rule(bucket, language):
    if bucket["season"] == "short-late":
        return (f"Sentence 2: the season. It MUST cite F03 and must contain exactly the words "
                f"\"{SEASON_PHRASE[language]}\". Never use the word \"marehemu\" or any word for \"late\".\n")
    return "Sentence 2: the season, citing one season fact ID. Never use the word \"marehemu\".\n"


def numbers(text):
    return {float(value.replace(",", ".")) for value in re.findall(r"\d+(?:[.,]\d+)?", text)}


def crops_named(text):
    return {crop for crop, words in CROP_WORDS.items()
            if any(re.search(rf"\b{re.escape(word)}", text, re.IGNORECASE) for word in words)}


def has_word(words, text):
    return [word for word in words if re.search(rf"\b{re.escape(word)}", text, re.IGNORECASE)]


def validate(raw, crop, month, bucket, language="en"):
    """Return (sentences, None) or (None, reason)."""
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None, "not valid JSON"
    sentences = data.get("sentences") if isinstance(data, dict) else data
    if not isinstance(sentences, list) or len(sentences) != 3:
        return None, f"expected 3 sentences, got {len(sentences) if isinstance(sentences, list) else 'none'}"
    allowed = set(allowed_facts(crop, month)) | {"DATA"}
    cleaned = []
    for sentence in sentences:
        if not isinstance(sentence, dict) or not isinstance(sentence.get("text"), str) \
                or not sentence["text"].strip() or not isinstance(sentence.get("facts", []), list):
            return None, "sentence is not {text, facts}"
        cited = [str(fact) for fact in sentence.get("facts", [])]
        unknown = [fact for fact in cited if fact not in allowed]
        if unknown:
            return None, f"fact not allowed for {crop} in month {month}: {unknown}"
        cleaned.append({"text": sentence["text"].strip(), "facts": cited})
    text = " ".join(sentence["text"] for sentence in cleaned)
    if re.search(r"\[\s*F\d|\bF\d{2}\b", text):
        return None, "fact tag inside the text (tags belong in the facts field)"
    weather = cleaned[0]["text"]
    if RAIN_TOKEN not in weather or TEMP_TOKEN not in weather:
        return None, f"weather sentence must contain {RAIN_TOKEN} and {TEMP_TOKEN} exactly"
    without_tokens = text.replace(RAIN_TOKEN, " ").replace(TEMP_TOKEN, " ")
    if re.search(r"\d", without_tokens):
        return None, f"digit outside the placeholders: {sorted(numbers(without_tokens))}"
    if re.search(r"\{[^}]*\}", without_tokens):
        return None, "unknown or misspelt placeholder"
    number_words = [word for words in NUMBER_WORDS.values() for word in words
                    if re.search(rf"\b{word}\b", text, re.IGNORECASE)]
    if number_words:
        return None, f"number written as a word: {number_words}"
    banned = has_word([word for words in BANNED.values() for word in words], text)
    if banned:
        return None, f"banned word: {banned}"
    advice = has_word([word for words in ADVICE_WORDS.values() for word in words], text)
    if advice:
        return None, f"advice or over-claiming word: {advice}"
    if has_word(["marehemu"], text):
        return None, "season described as late (marehemu)"
    wrong = [f"{word} (use {right})" for word, right in WRONG_VOCABULARY.items() if has_word([word], text)]
    if wrong:
        return None, f"wrong vocabulary: {wrong}"
    rain_phrase = RAIN_PHRASES[language][bucket["rain"]]
    if rain_phrase.lower() not in weather.lower():
        return None, f"weather sentence must say \"{rain_phrase}\""
    if language == "sw" and TEMP_PHRASE["sw"] not in weather.lower():
        return None, "weather sentence must say \"nyuzi joto\""
    if "F09" in cleaned[2]["facts"] and not has_word(PAST_WORDS[language], cleaned[2]["text"]):
        return None, f"F09 sentence must say it was in the past ({' / '.join(PAST_WORDS[language])})"
    missing_units = [pattern for pattern in UNIT_PATTERNS[language] if not re.search(pattern, weather)]
    if missing_units:
        return None, "weather sentence needs units: " + (
            "\"{RAIN} mm\" and \"{TEMP} degrees\"" if language == "en"
            else "\"milimita {RAIN}\" and \"nyuzi joto {TEMP}\"")
    if re.search(r"\bthis week\b|\bwiki hii\b", cleaned[1]["text"], re.IGNORECASE):
        return None, "season sentence must not say \"this week\" / \"wiki hii\""
    for sentence in cleaned:
        if "F07" in sentence["facts"]:
            if not has_word(F07_SOURCE, sentence["text"]):
                return None, "F07 sentence must name ICRISAT, KALRO or researchers (watafiti)"
            if re.search(FARMERS_FOUND, sentence["text"], re.IGNORECASE):
                return None, "F07 sentence must not say farmers found the result"
    if season(month)["key"] == "short-late":
        # "late" is fine in "late March" (long rains) but not as a word for the season itself.
        if re.search(r"\blate\b", cleaned[1]["text"], re.IGNORECASE):
            return None, "season described as late (late)"
        if "F03" not in cleaned[1]["facts"]:
            return None, "season sentence in November must cite F03"
        if SEASON_PHRASE[language].lower() not in cleaned[1]["text"].lower():
            return None, f"season sentence must say \"{SEASON_PHRASE[language]}\""
    crop_cited = set(cleaned[2]["facts"]) & set(CROP_FACTS[crop])
    if not crop_cited and not has_word(OFFICER_WORDS, cleaned[2]["text"]):
        return None, "sentence 3 cites no crop fact and does not refer to the extension officer"
    cited_all = {fact for sentence in cleaned for fact in sentence["facts"]}
    permitted = {crop} | {name for fact in cited_all for name in FACT_CROPS.get(fact, [])}
    others = crops_named(text) - permitted
    if others:
        return None, f"other crop named: {sorted(others)}"
    return cleaned, None


# =============================================================================
# Templates (fallback; also valid under validate)
# =============================================================================
SEASON_TEMPLATES = {
    "short": ("F02", "We are in the short rains, when about a quarter of the year's rain falls.",
              "Tuko katika msimu wa mvua fupi, wakati takriban robo ya mvua ya mwaka hunyesha."),
    "short-late": ("F03", "We are at the end of the short rains, which end in December.",
                   "Tuko katika mwisho wa msimu wa mvua fupi, ambao huisha mwezi Desemba."),
    "dry": ("F02", "This is the dry season, with only scattered rains.",
            "Huu ni msimu wa kiangazi, wenye mvua za hapa na pale tu."),
    "long": ("F02", "We are in the long rains, which are heaviest from late March to late May.",
             "Tuko katika msimu wa mvua ndefu, ambao hunyesha zaidi kati ya mwisho wa Machi na mwisho wa Mei."),
    "between": ("F02", "The long rains are over; the short rains usually come from August to October.",
                "Mvua ndefu zimekwisha; mvua fupi kwa kawaida hunyesha kuanzia Agosti hadi Oktoba."),
}
CROP_TEMPLATES = {
    "maize": ("F09", "Maize needs moist weather while it is flowering; in a past dry, hot spell in Busia, "
                     "flowering maize began to wilt.",
              "Mahindi yanahitaji unyevu wakati wa kuchanua; wakati wa ukame uliopita na joto kali huko Busia, "
              "mahindi yaliyokuwa yakichanua yalianza kunyauka."),
    "beans": ("F09", "Beans need moist weather while they are flowering; in a past dry, hot spell in Busia, "
                     "flowering beans began to wilt.",
              "Maharagwe yanahitaji unyevu wakati wa kuchanua; wakati wa ukame uliopita na joto kali huko Busia, "
              "maharagwe yaliyokuwa yakichanua yalianza kunyauka."),
    "sorghum": ("F07", "After failed rains in Busia, researchers from ICRISAT and KALRO reported that farmers who "
                       "grew sorghum instead of maize were hit less hard.",
                "Baada ya mvua kukosa Busia, watafiti wa ICRISAT na KALRO walieleza kuwa wakulima waliolima mtama "
                "badala ya mahindi waliathirika kidogo."),
    "cassava": ("F08", "Some farmers in Busia say cassava fed their families when the maize harvest failed.",
                "Baadhi ya wakulima wa Busia wanasema muhogo uliwasaidia familia zao mavuno ya mahindi "
                "yalipokosekana."),
    "cowpeas": ("F07", "After failed rains in Busia, researchers from ICRISAT and KALRO reported that growing "
                       "cowpeas instead of maize helped farmers.",
                "Baada ya mvua kukosa Busia, watafiti wa ICRISAT na KALRO walieleza kuwa kulima kunde badala ya "
                "mahindi kuliwasaidia wakulima."),
    "sweet potatoes": ("F01", "In Busia, sweet potatoes are grown mainly for food at home.",
                       "Huko Busia, viazi vitamu hulimwa hasa kwa chakula cha nyumbani."),
}
ASK_OFFICER = {"en": "For advice on {crop}, ask the agricultural extension officer.",
               "sw": "Kwa ushauri kuhusu {crop}, uliza afisa wa ugani wa kilimo."}


def template(bucket, crop, language):
    """Fallback sentences with {RAIN} and {TEMP} placeholders (filled per station by fill())."""
    if language == "sw":
        weather = (f"Wiki hii kunatarajiwa {RAIN_PHRASES['sw'][bucket['rain']]}, karibu milimita {RAIN_TOKEN} "
                   f"kwa siku, na joto la juu karibu nyuzi joto {TEMP_TOKEN}")
        weather += ("; hali ni sawa kila siku." if bucket["same_every_day"]
                    else "; hali inaweza kubadilika siku hadi siku.")
    else:
        phrase = RAIN_PHRASES["en"][bucket["rain"]]
        weather = (f"{phrase[0].upper() + phrase[1:]} is expected this week, about {RAIN_TOKEN} mm a day, "
                   f"with highs near {TEMP_TOKEN} degrees")
        weather += ("; the weather is similar every day." if bucket["same_every_day"]
                    else "; the weather may change from day to day.")
    index = 1 if language == "en" else 2
    season_fact, *season_texts = SEASON_TEMPLATES[bucket["season"]]
    crop_fact, *crop_texts = CROP_TEMPLATES[crop]
    if crop_fact in allowed_facts(crop, bucket["month"]):
        note = {"text": crop_texts[index - 1], "facts": [crop_fact]}
    else:
        note = {"text": ASK_OFFICER[language].format(crop=CROP_NAMES[language][crop]), "facts": []}
    return [{"text": weather, "facts": ["DATA"]},
            {"text": season_texts[index - 1], "facts": [season_fact]}, note]


def fill(sentences, bucket):
    """Replace the placeholders with one station's rounded rain (mm) and highest temperature."""
    return [{**sentence, "text": sentence["text"].replace(RAIN_TOKEN, str(bucket["rain_mm"]))
                                                 .replace(TEMP_TOKEN, str(bucket["temp_max"]))}
            for sentence in sentences]


# =============================================================================
# Gemini calls
# =============================================================================
class Stop(Exception):
    """An HTTP or network error: no further calls in this run."""


def version(name):
    match = re.search(r"gemini-(\d+(?:\.\d+)*)", name)
    return tuple(int(part) for part in match.group(1).split(".")) if match else (0,)


def pick_model(names):
    """Newest text flash-lite model (else newest text flash); same rule as gemini_smoke_test.py."""
    text_models = [name for name in names if not re.search(r"tts|image|audio|live|omni", name)]
    lite = [name for name in text_models if "flash-lite" in name]
    candidates = lite or text_models
    stable = lambda name: not re.search(r"preview|exp", name)
    return max(candidates, key=lambda name: (version(name), stable(name), name))


def unavailable(response):
    """404, or an error saying the model is no longer available: skip to the next model."""
    if response.ok:
        return False
    return response.status_code == 404 or "no longer available" in http_error(response).lower()


def quota_exceeded(response):
    """HTTP 429 that mentions a quota or a per-day limit: skip this model for the rest of the run."""
    if response.status_code != 429:
        return False
    message = http_error(response).lower()
    return "quota" in message or "per day" in message


def http_error(response):
    try:
        message = response.json().get("error", {}).get("message", "")
    except ValueError:
        message = response.text[:200]
    return f"HTTP {response.status_code}: {message}"


def is_text_model(name):
    return not re.search(r"tts|image|audio|live|omni", name)


def model_chain(default, names):
    """Default model, then the fallbacks that are listed text models (no duplicates)."""
    chain = [default]
    for name in FALLBACK_MODELS:
        if name in names and is_text_model(name) and name not in chain:
            chain.append(name)
    return chain


def choose_models(session, key):
    """Model chain: GEMINI_MODEL or the newest text flash-lite model, then the fallbacks."""
    response = session.get(f"{API}/models", headers={"x-goog-api-key": key}, params={"pageSize": 1000},
                           timeout=30)
    if not response.ok:
        raise Stop(http_error(response))
    names = [model["name"] for model in response.json().get("models", [])
             if "generateContent" in model.get("supportedGenerationMethods", []) and "flash" in model["name"]]
    override = os.getenv("GEMINI_MODEL")
    if override:
        default = override if override.startswith("models/") else f"models/{override}"
    elif names:
        default = pick_model(names)
    else:
        raise Stop("no flash model supports generateContent")
    return model_chain(default, names)


class Generator:
    def __init__(self, chain, key, session, force=False, sleep=4.0, max_calls=MAX_CALLS, wait=time.sleep,
                 log=print, minutes=None):
        self.chain, self.index = list(chain), 0
        self.key, self.session = key, session
        self.force, self.sleep, self.max_calls = force, sleep, max_calls
        self.wait, self.log = wait, log
        self.calls = self.cache_hits = self.retries = 0
        self.calls_per_model = {}
        self.stopped, self.skipped = None, []
        self.deadline = time.monotonic() + minutes * 60 if minutes else None

    @property
    def model(self):
        return self.chain[self.index]

    @staticmethod
    def cache_path(text):
        """Keyed by the prompt only, so an answer from any model in the chain is reused."""
        return CACHE / f"{hashlib.sha256(text.encode('utf-8')).hexdigest()}.json"

    def cached(self, text):
        """(response, model that produced it) or (None, None)."""
        path = self.cache_path(text)
        if not self.force and path.is_file():
            self.cache_hits += 1
            stored = json.loads(path.read_text(encoding="utf-8"))
            return stored["response"], stored.get("model")
        return None, None

    def store(self, text, response, model):
        CACHE.mkdir(parents=True, exist_ok=True)
        self.cache_path(text).write_text(json.dumps({"model": model, "response": response}), encoding="utf-8")

    def post(self, text):
        """One HTTP request to the current model (counts towards the cap)."""
        if self.calls >= self.max_calls:
            self.stopped = f"call cap of {self.max_calls} reached"
            raise Stop(self.stopped)
        if self.deadline is not None and time.monotonic() >= self.deadline:
            self.stopped = "time limit reached"
            raise Stop(self.stopped)
        if self.calls:
            self.wait(self.sleep)
        self.calls += 1
        self.calls_per_model[self.model] = self.calls_per_model.get(self.model, 0) + 1
        body = {"contents": [{"role": "user", "parts": [{"text": text}]}],
                "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2}}
        return self.session.post(f"{API}/{self.model}:generateContent",
                                 headers={"x-goog-api-key": self.key}, json=body, timeout=60)

    def call(self, text):
        """Response text. 503/timeout: retry 3 times, then the next model; other errors stop all calls."""
        if self.stopped:
            raise Stop(self.stopped)
        while True:
            problem = None
            for attempt, pause in enumerate([0] + RETRY_WAITS):
                if attempt:
                    self.retries += 1
                    self.wait(pause)
                try:
                    response = self.post(text)
                except requests.exceptions.Timeout:
                    problem = "network timeout"
                    continue
                except requests.exceptions.RequestException as error:
                    self.stopped = f"network error: {type(error).__name__}"
                    raise Stop(self.stopped)
                if response.status_code == 503:
                    problem = http_error(response)
                    continue
                if unavailable(response) or quota_exceeded(response):
                    # This model cannot be used for the rest of the run: skip it, no retries.
                    problem = http_error(response)
                    self.skipped.append(f"{self.model}: {problem}")
                    break
                if not response.ok:  # any other 429 or 4xx, other 5xx
                    self.stopped = http_error(response)
                    raise Stop(self.stopped)
                candidates = response.json().get("candidates") or []
                parts = candidates[0].get("content", {}).get("parts", []) if candidates else []
                return "".join(part.get("text", "") for part in parts)
            if self.index + 1 < len(self.chain):
                self.log(f"{self.model}: {problem}; falling back to {self.chain[self.index + 1]}")
                self.index += 1
                continue
            self.stopped = f"{problem} (no model left in the chain: {', '.join(self.chain)})"
            raise Stop(self.stopped)

    def from_cache(self, bucket, crop, language, facts):
        """A cached answer that passes the current validation, with no API call: (sentences, model)."""
        raw, model = self.cached(prompt(bucket, crop, language, facts))
        if raw is None:
            return None, None
        result, _ = validate(raw, crop, bucket["month"], bucket, language)
        return (result, model) if result else (None, None)

    def sentences(self, bucket, crop, language, facts, failures):
        """(validated AI sentences, model) or (None, None) to use the template."""
        text = prompt(bucket, crop, language, facts)
        raw, model = self.cached(text)
        if raw is not None:
            result, reason = validate(raw, crop, bucket["month"], bucket, language)
            if result:
                return result, model
        raw = self.call(text)
        result, reason = validate(raw, crop, bucket["month"], bucket, language)
        if result:
            self.store(text, raw, self.model)
            return result, self.model
        failures.append(f"{crop}/{language}: {reason} (retrying once)")
        retry = f"{text}\n\nYour previous answer was rejected ({reason}). Return corrected JSON only."
        raw = self.call(retry)
        result, reason = validate(raw, crop, bucket["month"], bucket, language)
        if result:
            self.store(text, raw, self.model)
            return result, self.model
        failures.append(f"{crop}/{language}: {reason} (template used)")
        return None, None


# =============================================================================
# Build
# =============================================================================
def read_inputs(deploy=DEPLOY):
    daily = pd.read_csv(deploy / "forecast_daily.csv", dtype={"device_id": "string"})
    status = pd.read_csv(deploy / "station_status.csv", dtype={"device_id": "string"})
    path = deploy / "insights_latest.csv"
    insights = pd.read_csv(path, dtype={"device_id": "string"}) if path.is_file() else pd.DataFrame()
    return daily.dropna(subset=["rain_mm", "temp_max"]), status, insights


def short_ids(device_ids):
    """Shortest unique ending (at least 3 digits); the same rule as app.short_ids and the site's station ids."""
    ids = sorted(set(device_ids))
    for size in range(3, max([len(value) for value in ids] + [3]) + 1):
        endings = [value[-size:] for value in ids]
        if len(set(endings)) == len(endings):
            return dict(zip(ids, endings))
    return {value: value for value in ids}


def build(deploy=DEPLOY, facts_path=FACTS, generator=None, log=print):
    daily, status, insights = read_inputs(deploy)
    facts = load_facts(facts_path)
    station_buckets = buckets(daily, insights)
    areas = status.set_index("device_id").area.to_dict() if "area" in status else {}
    # Keyed by the site's short station id ("001"), never the raw device number.
    endings = short_ids(list(status.device_id) + list(station_buckets))
    failures, memo, distinct = [], {}, []
    counts = {"ai": 0, "template": 0}
    stations, stop_logged = {}, False
    for device_id, bucket in station_buckets.items():
        crops = {}
        for crop in CROPS:
            crops[crop] = {}
            for language in LANGUAGES:
                key = prompt(bucket, crop, language, facts)
                if key not in memo:
                    result, model = None, None
                    if generator is not None and not generator.stopped:
                        try:
                            result, model = generator.sentences(bucket, crop, language, facts, failures)
                        except Stop as stop:
                            if not stop_logged:
                                log(f"Calls stopped: {stop}")
                                stop_logged = True
                    if generator is not None and generator.stopped and not result:
                        # No more calls, but a valid cached answer is still used.
                        result, model = generator.from_cache(bucket, crop, language, facts)
                    memo[key] = ({"sentences": result, "source": "ai", "model": model} if result else
                                 {"sentences": template(bucket, crop, language), "source": "template",
                                  "model": None})
                    distinct.append((crop, language, memo[key]))
                entry = memo[key]  # shared by every station with the same labels; numbers filled below
                counts[entry["source"]] += 1
                crops[crop][language] = {**entry, "sentences": fill(entry["sentences"], bucket)}
        public = {name: bucket[name] for name in ["rain_mm", "temp_max", "same_every_day", "season"]}
        stations[endings[device_id]] = {"area": areas.get(device_id), "bucket": public, "crops": crops}
    models_used = sorted({entry["model"] for entry in memo.values() if entry["model"]})
    output = {
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        # Null unless at least one entry came from the AI; each entry records its own model.
        "model": (generator.chain[0] if generator and generator.chain[0] in models_used
                  else models_used[0] if models_used else None),
        "models_used": models_used,
        "stations": stations,
    }
    return output, {"distinct_prompts": len(memo), "counts": counts, "failures": failures,
                    "models_used": models_used, "distinct": distinct}


def main(args):
    daily, status, insights = read_inputs()
    facts = load_facts(args.facts)
    station_buckets = buckets(daily, insights)
    distinct = {prompt(bucket, crop, language, facts)
                for bucket in station_buckets.values() for crop in CROPS for language in LANGUAGES}
    print(f"Stations with a forecast: {len(station_buckets)}")
    for device_id, bucket in station_buckets.items():
        print(f"  ...{device_id[-4:]}: rain {bucket['rain_mm']} mm ({RAIN_LABELS['en'][bucket['rain']]}), "
              f"max {bucket['temp_max']} ({TEMP_LABELS['en'][bucket['temp']]}), "
              f"same every day {bucket['same_every_day']}, season {bucket['season']}")
    print(f"Distinct prompts: {len(distinct)} (crops x languages x distinct buckets)")
    if args.dry_run:
        return

    generator = None
    if args.cache_only:
        # No key and no HTTP calls: valid cached answers, templates for the rest.
        generator = Generator(["cache"], None, None)
        generator.stopped = "cache only (no calls)"
        print("Cache only: no API calls.")
    elif not args.templates_only:
        from dotenv import load_dotenv
        import requests

        load_dotenv(ROOT / ".env")
        key = os.getenv("GEMINI_API_KEY")
        if not key:
            print("GEMINI_API_KEY is not set: using templates only.")
        else:
            session = requests.Session()
            try:
                chain = choose_models(session, key)
                print(f"Model chain: {' -> '.join(chain)}")
                generator = Generator(chain, key, session, force=args.force, sleep=args.sleep,
                                      minutes=args.minutes)
            except Stop as stop:
                print(f"Model selection failed ({stop}): using templates only.")
    output, summary = build(facts_path=args.facts, generator=generator)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(output, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nWrote {OUTPUT} ({OUTPUT.stat().st_size:,} bytes)")
    print(f"Distinct prompts: {summary['distinct_prompts']}")
    if generator:
        print(f"Calls made: {generator.calls}; retries: {generator.retries}; cache hits: {generator.cache_hits}"
              + (f"; stopped: {generator.stopped}" if generator.stopped else ""))
        print(f"Calls per model: {generator.calls_per_model}")
    if generator and generator.skipped:
        print("Models skipped (unavailable or quota):")
        for line in generator.skipped:
            print(f"  {line}")
    print(f"Entries: ai {summary['counts']['ai']}, template {summary['counts']['template']}")
    per_model = {}
    for station in output["stations"].values():
        for languages in station["crops"].values():
            for entry in languages.values():
                per_model[entry["model"] or "template"] = per_model.get(entry["model"] or "template", 0) + 1
    print(f"Entries per model: {per_model}")
    print("Validation failures:" if summary["failures"] else "Validation failures: none")
    for failure in summary["failures"]:
        print(f"  {failure}")
    if args.print_texts:
        print("\nDistinct texts (placeholders not filled):")
        for crop, language, entry in summary["distinct"]:
            label = f"AI, {entry['model']}" if entry["source"] == "ai" else "template"
            print(f"\n[{crop} / {language}] ({label})")
            for sentence in entry["sentences"]:
                print(f"  {sentence['text']}  {sentence['facts']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="print buckets and prompt count; no calls")
    parser.add_argument("--templates-only", action="store_true", help="no calls; templates for every entry")
    parser.add_argument("--force", action="store_true", help="ignore the cache")
    parser.add_argument("--facts", type=Path, default=FACTS)
    parser.add_argument("--sleep", type=float, default=4.0, help="seconds between calls")
    parser.add_argument("--minutes", type=float, default=None, help="stop making calls after this many minutes")
    parser.add_argument("--print-texts", action="store_true", help="print every distinct text with placeholders")
    parser.add_argument("--cache-only", action="store_true", help="no calls; valid cached answers, else templates")
    main(parser.parse_args())
