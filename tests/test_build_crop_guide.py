"""Tests for scripts/build_crop_guide.py: validation, templates, model chain and output safety."""

import json
import re
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import build_crop_guide as cg  # noqa: E402

FACTS_TEXT = "\n".join(f"F0{n} | Fact number {n} text. | Source {n}. |" for n in range(1, 10))
FACT_DICT = {f"F0{n}": f"Fact number {n} text." for n in range(1, 10)}
BUCKET = {"rain_mm": 8, "rain": 2, "temp_max": 29, "temp": 1, "same_every_day": True,
          "season": "short-late", "month": 11}
DISCLAIMER = re.compile(r"\bAI\b|akili bandia|imetengenezwa na|generated|artificial intelligence",
                        re.IGNORECASE)
LABEL_LEAKS = re.compile(r"\[F|\(warm\)|\(joto\)|\{RAIN\}|\{TEMP\}")


def answer(*sentences):
    return json.dumps({"sentences": [{"text": text, "facts": facts} for text, facts in sentences]})


GOOD = answer(("Moderate rain of about {RAIN} mm a day, with highs near {TEMP} degrees, similar every day.",
               ["DATA"]),
              ("We are at the end of the short rains.", ["F03"]),
              ("In a past dry spell, flowering maize needed moist weather.", ["F09"]))
SW_GOOD = answer(("Wiki hii kunatarajiwa mvua ya wastani, karibu milimita {RAIN} kwa siku, na joto la juu "
                  "karibu nyuzi joto {TEMP}; hali ni sawa kila siku.", ["DATA"]),
                 ("Tuko katika mwisho wa msimu wa mvua fupi.", ["F03"]),
                 ("Baadhi ya wakulima wa Busia wanasema muhogo uliwasaidia familia zao.", ["F08"]))


def check(raw, crop="maize", language="en"):
    return cg.validate(raw, crop, 11, BUCKET, language)


# ----------------------------------------------------------------------------- validation
def test_validate_accepts_good_answers():
    sentences, reason = check(GOOD)
    assert reason is None and len(sentences) == 3 and sentences[1]["facts"] == ["F03"]
    assert check(SW_GOOD, "cassava", "sw")[1] is None


@pytest.mark.parametrize("raw, crop, language, expected", [
    # placeholders and numbers
    (GOOD.replace("{RAIN}", "8"), "maize", "en", "must contain {RAIN} and {TEMP}"),
    (GOOD.replace("{TEMP}", "{temp}"), "maize", "en", "must contain {RAIN} and {TEMP}"),
    (GOOD.replace("similar every day", "similar every day, {WIND}"), "maize", "en", "placeholder"),
    (GOOD.replace("similar every day.", "similar on all 7 days."), "maize", "en", "digit outside"),
    (GOOD.replace("similar every day", "similar for seven days"), "maize", "en", "number written as a word"),
    (SW_GOOD.replace("kila siku", "kwa siku saba"), "cassava", "sw", "number written as a word"),
    # banned and advice words
    (GOOD.replace("moist weather", "fertiliser"), "maize", "en", "banned word"),
    (SW_GOOD.replace("uliwasaidia", "na dawa uliwasaidia"), "cassava", "sw", "banned word"),
    (GOOD.replace("In a past dry spell,", "You should know that in a past dry spell,"), "maize", "en", "advice"),
    (GOOD.replace("In a past", "Studies show that in a past"), "maize", "en", "advice"),
    (SW_GOOD.replace("Baadhi ya wakulima", "Wakulima, familia nyingi,"), "cassava", "sw", "advice"),
    (SW_GOOD.replace("wanasema", "zingatia kwamba wanasema"), "cassava", "sw", "advice"),
    # facts and crops
    (GOOD.replace('"F03"', '"F99"'), "maize", "en", "fact not allowed"),
    (GOOD.replace('"F09"', '"F08"'), "maize", "en", "fact not allowed"),  # F08 is cassava's fact
    (GOOD.replace("flowering maize", "flowering maize and cassava"), "maize", "en", "other crop named"),
    (answer(("Moderate rain, {RAIN} mm, {TEMP} degrees.", ["DATA"]),
            ("We are at the end of the short rains.", ["F03"])), "maize", "en", "expected 3 sentences"),
    ("not json", "maize", "en", "not valid JSON"),
    (GOOD.replace("the short rains.", "the short rains [F03]."), "maize", "en", "fact tag"),
    # season rules
    (SW_GOOD.replace("mvua fupi.", "mvua fupi za marehemu."), "cassava", "sw", "marehemu"),
    (GOOD.replace("We are at the end of the short rains.", "These are the late short rains, the end of the "
                  "short rains."), "maize", "en", "late"),
    (GOOD.replace('["F03"]', '["F02"]'), "maize", "en", "must cite F03"),
    (GOOD.replace("We are at the end of the short rains.", "The short rains finish soon."), "maize", "en",
     "must say"),
    (SW_GOOD.replace("Tuko katika mwisho wa msimu wa mvua fupi.", "Msimu wa mvua fupi unaisha."), "cassava",
     "sw", "must say"),
    # sentence 3
    (GOOD.replace('"F09"', '"F03"'), "maize", "en", "cites no crop fact"),
    (GOOD.replace('"F09"]', ']'), "maize", "en", "cites no crop fact"),
])
def test_validate_rejects(raw, crop, language, expected):
    sentences, reason = check(raw, crop, language)
    assert sentences is None and expected in reason, reason


@pytest.mark.parametrize("raw, crop, language, expected", [
    (SW_GOOD.replace("mvua fupi.", "mvua fupi, ambao huisha Disemba."), "cassava", "sw", "Disemba"),
    (SW_GOOD.replace("nyuzi joto", "digrii"), "cassava", "sw", "digrii"),
    (SW_GOOD.replace("mvua ya wastani", "mvua ya kiasi"), "cassava", "sw", "mvua ya kiasi"),
    (SW_GOOD.replace("nyuzi joto {TEMP}", "joto {TEMP}"), "cassava", "sw", "nyuzi joto"),
    (GOOD.replace("Moderate rain", "Some rain"), "maize", "en", "moderate rain"),
])
def test_fixed_vocabulary(raw, crop, language, expected):
    sentences, reason = check(raw, crop, language)
    assert sentences is None and expected in reason, reason


def test_f09_sentence_must_say_it_was_in_the_past():
    no_past = GOOD.replace("In a past dry spell,", "In a dry spell,")
    assert "past" in check(no_past)[1]
    assert check(GOOD.replace("In a past dry spell,", "In an earlier dry spell,"))[1] is None
    sw = SW_GOOD.replace("Baadhi ya wakulima wa Busia wanasema muhogo uliwasaidia familia zao.",
                         "Wakati wa ukame, mahindi yaliyokuwa yakichanua yalinyauka.").replace('"F08"', '"F09"')
    assert "past" in check(sw, "maize", "sw")[1]
    assert check(sw.replace("Wakati wa ukame,", "Wakati wa ukame uliopita,"), "maize", "sw")[1] is None


def test_other_crop_words_are_allowed_only_when_the_cited_fact_names_them():
    cowpea_f06 = SW_GOOD.replace("Baadhi ya wakulima wa Busia wanasema muhogo uliwasaidia familia zao.",
                                 "Watafiti walieleza kuwa kunde na dengu zilisaidia.").replace('"F08"', '"F06"')
    assert "other crop named" in check(cowpea_f06, "cowpeas", "sw")[1]  # F06 names cowpeas only
    cowpea_f07 = cowpea_f06.replace('"F06"', '"F07"')
    assert check(cowpea_f07, "cowpeas", "sw")[1] is None  # F07 names green grams (dengu)
    for word in ["ulezi", "mbaazi", "kunde", "dengu", "millet", "pigeon pea", "green gram", "cowpea"]:
        assert cg.crops_named(f"Text with {word}."), word


def test_sentence_three_may_refer_to_the_extension_officer():
    raw = GOOD.replace("In a past dry spell, flowering maize needed moist weather.",
                       "For advice on maize, ask the agricultural extension officer.").replace('"F09"]', ']')
    assert check(raw)[1] is None


def test_crop_named_in_a_cited_fact_is_allowed():
    raw = GOOD.replace("In a past dry spell, flowering maize needed moist weather.",
                       "KALRO reported that farmers who grew sorghum instead of maize were hit less hard."
                       ).replace('"F09"', '"F07"')
    assert check(raw, "sorghum")[1] is None


SORGHUM_F07 = GOOD.replace("In a past dry spell, flowering maize needed moist weather.",
                           "Researchers from ICRISAT and KALRO reported that growing sorghum instead of maize "
                           "helped.").replace('"F09"', '"F07"')


@pytest.mark.parametrize("raw, crop, language, expected", [
    # units next to the placeholders
    (GOOD.replace("{RAIN} mm", "{RAIN}"), "maize", "en", "needs units"),
    (GOOD.replace("{TEMP} degrees", "{TEMP}"), "maize", "en", "needs units"),
    (SW_GOOD.replace("milimita {RAIN}", "{RAIN} milimita"), "cassava", "sw", "needs units"),
    (SW_GOOD.replace("nyuzi joto {TEMP}", "{TEMP} nyuzi joto"), "cassava", "sw", "needs units"),
    # season sentence must not say this week
    (GOOD.replace("We are at the end of the short rains.", "This week marks the end of the short rains."),
     "maize", "en", "this week"),
    (SW_GOOD.replace("Tuko katika mwisho wa msimu wa mvua fupi.", "Wiki hii ni mwisho wa msimu wa mvua fupi."),
     "cassava", "sw", "wiki hii"),
    # F07: name the researchers, not farmers
    (SORGHUM_F07.replace("Researchers from ICRISAT and KALRO reported that growing", "Growing"), "sorghum",
     "en", "must name ICRISAT"),
    (SORGHUM_F07.replace("Researchers from ICRISAT and KALRO reported that growing",
                         "Farmers found, says KALRO, that growing"), "sorghum", "en", "farmers found"),
    (SW_GOOD.replace("Baadhi ya wakulima wa Busia wanasema muhogo uliwasaidia familia zao.",
                     "Wakulima waligundua, kwa mujibu wa KALRO, kuwa kunde zilisaidia.").replace('"F08"', '"F07"'),
     "cowpeas", "sw", "farmers found"),
    (SW_GOOD.replace("Baadhi ya wakulima wa Busia wanasema muhogo uliwasaidia familia zao.",
                     "Kunde zilisaidia baada ya mvua kukosa.").replace('"F08"', '"F07"'), "cowpeas", "sw",
     "must name ICRISAT"),
    # sorghum may no longer cite F01
    (SORGHUM_F07.replace('"F07"', '"F01"'), "sorghum", "en", "fact not allowed"),
])
def test_validate_rejects_units_week_f07_and_sorghum_f01(raw, crop, language, expected):
    sentences, reason = check(raw, crop, language)
    assert sentences is None and expected in reason, reason


def test_f07_sentence_naming_the_researchers_passes():
    assert check(SORGHUM_F07, "sorghum")[1] is None
    sw = SW_GOOD.replace("Baadhi ya wakulima wa Busia wanasema muhogo uliwasaidia familia zao.",
                         "Watafiti wa KALRO walieleza kuwa kunde zilisaidia.").replace('"F08"', '"F07"')
    assert check(sw, "cowpeas", "sw")[1] is None
    assert cg.CROP_FACTS["sorghum"] == ["F07"]
    assert all("F01" in cg.CROP_FACTS[crop] for crop in ["maize", "beans", "cassava", "sweet potatoes"])


# ----------------------------------------------------------------------------- templates and numbers
@pytest.mark.parametrize("month", [1, 4, 7, 9, 11])
@pytest.mark.parametrize("crop", cg.CROPS)
@pytest.mark.parametrize("language", cg.LANGUAGES)
@pytest.mark.parametrize("rain", range(4))
def test_templates_pass_validation(month, crop, language, rain):
    for same in (True, False):
        bucket = {**BUCKET, "rain": rain, "same_every_day": same, "month": month,
                  "season": cg.season(month)["key"]}
        sentences = cg.template(bucket, crop, language)
        assert cg.validate(json.dumps({"sentences": sentences}), crop, month, bucket, language)[1] is None
        text = " ".join(sentence["text"] for sentence in cg.fill(sentences, bucket))
        assert not DISCLAIMER.search(text) and not LABEL_LEAKS.search(text), text


def test_template_wording():
    sw = cg.fill(cg.template(BUCKET, "sweet potatoes", "sw"), BUCKET)
    assert sw[0]["text"].startswith("Wiki hii kunatarajiwa mvua ya wastani, karibu milimita 8 kwa siku")
    assert "joto la juu karibu nyuzi joto 29;" in sw[0]["text"]
    assert sw[2]["text"] == "Huko Busia, viazi vitamu hulimwa hasa kwa chakula cha nyumbani."
    en = cg.fill(cg.template(BUCKET, "maize", "en"), BUCKET)
    assert en[0]["text"] == ("Moderate rain is expected this week, about 8 mm a day, with highs near 29 "
                             "degrees; the weather is similar every day.")


def test_fill_puts_each_stations_numbers_in():
    sentences = json.loads(GOOD)["sentences"]
    first = cg.fill(sentences, {**BUCKET, "rain_mm": 8, "temp_max": 29})[0]["text"]
    second = cg.fill(sentences, {**BUCKET, "rain_mm": 6, "temp_max": 30})[0]["text"]
    assert "about 8 mm" in first and "near 29 degrees" in first
    assert "about 6 mm" in second and "near 30 degrees" in second
    assert sentences[0]["text"].count("{RAIN}") == 1, "fill must not change the shared sentences"


def test_prompt_has_labels_only_so_stations_share_it():
    a = cg.prompt({**BUCKET, "rain_mm": 8, "temp_max": 29}, "maize", "sw", FACT_DICT)
    b = cg.prompt({**BUCKET, "rain_mm": 6, "temp_max": 30}, "maize", "sw", FACT_DICT)
    assert a == b and "{RAIN}" in a and "{TEMP}" in a
    assert not re.search(r"\b(6|8|29|30)\b", a)


# ----------------------------------------------------------------------------- build output
def write_deploy(folder):
    dates = pd.date_range("2026-11-25", periods=7).strftime("%Y-%m-%d")
    daily = pd.concat([
        pd.DataFrame({"device_id": "101990961244800001", "date": dates, "rain_mm": 8.0, "rain_upper": 100.0,
                      "temp_min": None, "temp_max": 29.4, "wind_max": 1.0}),
        pd.DataFrame({"device_id": "101990961244800395", "date": dates, "rain_mm": 5.9, "rain_upper": 80.0,
                      "temp_min": None, "temp_max": 30.3, "wind_max": 1.2})])
    status = pd.DataFrame({"device_id": ["101990961244800001", "101990961244800395"],
                           "area": ["Butula", "Nambale"], "active": [True, True]})
    insights = pd.DataFrame({"device_id": ["101990961244800001", "101990961244800395"],
                             "forecast_variation_mm": [0.0, 0.0]})
    daily.to_csv(folder / "forecast_daily.csv", index=False)
    status.to_csv(folder / "station_status.csv", index=False)
    insights.to_csv(folder / "insights_latest.csv", index=False)


def build(tmp_path, generator=None):
    write_deploy(tmp_path)
    facts = tmp_path / "facts.md"
    facts.write_text(FACTS_TEXT, encoding="utf-8")
    return cg.build(deploy=tmp_path, facts_path=facts, generator=generator, log=lambda *_: None)


def entries(output):
    return [entry for station in output["stations"].values() for crop in station["crops"].values()
            for entry in crop.values()]


def test_template_fallback_without_a_key(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    output, summary = build(tmp_path)
    assert all(entry["source"] == "template" and len(entry["sentences"]) == 3 for entry in entries(output))
    assert summary["counts"] == {"ai": 0, "template": 24}
    assert summary["distinct_prompts"] == 12, "both stations share one bucket"
    assert output["model"] is None and output["models_used"] == []


class FakeGenerator:
    """Stands in for Gemini: returns a fixed valid or invalid answer."""

    model = "models/fake-flash-lite"
    chain = [model]
    stopped = None

    def __init__(self, raw):
        self.raw = raw

    def sentences(self, bucket, crop, language, facts, failures):
        result, reason = cg.validate(self.raw, crop, bucket["month"], bucket, "en")
        if reason:
            failures.append(reason)
            return None, None
        return result, self.model


def test_shared_answer_gets_each_stations_numbers(tmp_path):
    output, summary = build(tmp_path, FakeGenerator(GOOD))
    first = output["stations"]["101990961244800001"]["crops"]["maize"]["en"]
    other = output["stations"]["101990961244800395"]["crops"]["maize"]["en"]
    assert first["source"] == other["source"] == "ai"
    assert "about 8 mm" in first["sentences"][0]["text"] and "near 29 degrees" in first["sentences"][0]["text"]
    assert "about 6 mm" in other["sentences"][0]["text"] and "near 30 degrees" in other["sentences"][0]["text"]


def test_output_has_source_and_model_fields_no_disclaimer_and_no_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaFAKEKEYFAKEKEYFAKEKEYFAKEKEYFAKE123")
    output, summary = build(tmp_path, FakeGenerator(GOOD))
    assert {entry["source"] for entry in entries(output)} == {"ai", "template"}
    for entry in entries(output):
        assert (entry["model"] is None) == (entry["source"] == "template")
        body = " ".join(sentence["text"] for sentence in entry["sentences"])
        assert not DISCLAIMER.search(body) and not LABEL_LEAKS.search(body), body
    assert output["model"] == FakeGenerator.model
    text = json.dumps(output)
    assert "AIza" not in text and "x-goog-api-key" not in text.lower()
    assert not re.search(r"[A-Za-z0-9_\-]{35,}", text), "key-like string in output"
    assert "Write a short weekly crop note" not in text, "raw prompt in output"


# ----------------------------------------------------------------------------- calls, retries, chain
class FakeResponse:
    def __init__(self, status, text=GOOD, message=None):
        self.status_code, self.ok = status, status < 400
        self._text, self.text = text, "error"
        self.message = message or f"status {status}"

    def json(self):
        if self.ok:
            return {"candidates": [{"content": {"parts": [{"text": self._text}]}}]}
        return {"error": {"message": self.message}}


class FakeSession:
    """Returns queued statuses (or (status, message)) per model; records the models called."""

    def __init__(self, plan):
        self.plan, self.called = plan, []

    def post(self, url, headers, json, timeout):
        model = url.split("/", 4)[-1].split(":")[0]
        self.called.append(model)
        step = self.plan.get(model, [])
        status = step.pop(0) if step else 200
        if status == "timeout":
            raise cg.requests.exceptions.Timeout()
        if isinstance(status, tuple):
            return FakeResponse(status[0], message=status[1])
        return FakeResponse(status)


def generator(plan, chain=("models/a", "models/b")):
    session = FakeSession(plan)
    gen = cg.Generator(list(chain), "k", session, force=True, wait=lambda seconds: None, log=lambda *_: None)
    return gen, session


@pytest.fixture(autouse=True)
def cache_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(cg, "CACHE", tmp_path / "cache")


def test_503_is_retried_three_times_then_falls_back():
    gen, session = generator({"models/a": [503, 503, 503, 503]})
    result, model = gen.sentences(BUCKET, "maize", "en", FACT_DICT, [])
    assert result and model == "models/b"
    assert session.called == ["models/a"] * 4 + ["models/b"] and gen.retries == 3
    assert gen.calls_per_model == {"models/a": 4, "models/b": 1}


def test_timeout_is_retried_like_503():
    gen, session = generator({"models/a": ["timeout", 503]})
    assert gen.sentences(BUCKET, "maize", "en", FACT_DICT, [])[1] == "models/a"
    assert gen.retries == 2


@pytest.mark.parametrize("status", [(429, "Too many requests per minute"), 400, 403, 500])
def test_other_errors_stop_all_calls(status):
    gen, session = generator({"models/a": [status]})
    with pytest.raises(cg.Stop):
        gen.sentences(BUCKET, "maize", "en", FACT_DICT, [])
    assert session.called == ["models/a"] and gen.stopped
    with pytest.raises(cg.Stop):
        gen.call("another prompt")
    assert session.called == ["models/a"], "no call after a stop"


@pytest.mark.parametrize("status", [
    (429, "You exceeded your current quota. Generate content requests per day limit exceeded."),
    (429, "Requests per day limit reached"),
    (404, "models/a is not found"),
    (400, "This model is no longer available to new users."),
])
def test_quota_or_unavailable_skips_the_model_for_the_rest_of_the_run(status):
    gen, session = generator({"models/a": [status]})
    result, model = gen.sentences(BUCKET, "maize", "en", FACT_DICT, [])
    assert result and model == "models/b" and gen.stopped is None and gen.retries == 0
    assert session.called == ["models/a", "models/b"] and len(gen.skipped) == 1
    gen.call("another prompt")
    assert session.called[-1] == "models/b", "skipped model is not used again"


def test_all_models_failing_stops():
    gen, session = generator({"models/a": [503] * 4, "models/b": [503] * 4})
    with pytest.raises(cg.Stop):
        gen.sentences(BUCKET, "maize", "en", FACT_DICT, [])
    assert len(session.called) == 8 and "no model left" in gen.stopped


def test_call_cap_includes_retries():
    gen, session = generator({"models/a": [503] * 4})
    gen.max_calls = 3
    with pytest.raises(cg.Stop):
        gen.sentences(BUCKET, "maize", "en", FACT_DICT, [])
    assert len(session.called) == 3 and "cap" in gen.stopped


def test_time_limit_stops_calls():
    gen, session = generator({})
    gen.deadline = 0  # already past
    with pytest.raises(cg.Stop):
        gen.sentences(BUCKET, "maize", "en", FACT_DICT, [])
    assert session.called == [] and "time limit" in gen.stopped


def test_cache_is_shared_across_models_and_keeps_the_answering_model():
    first, _ = generator({}, chain=("models/a",))
    first.force = False
    assert first.sentences(BUCKET, "maize", "en", FACT_DICT, [])[1] == "models/a"
    second, session = generator({}, chain=("models/b",))
    second.force = False
    result, model = second.sentences(BUCKET, "maize", "en", FACT_DICT, [])
    assert result and model == "models/a" and session.called == [] and second.cache_hits == 1


def test_cached_answers_are_still_used_after_calls_stop(tmp_path):
    write_deploy(tmp_path)
    facts_path = tmp_path / "facts.md"
    facts_path.write_text(FACTS_TEXT, encoding="utf-8")
    facts = cg.load_facts(facts_path)
    bucket = cg.buckets(*cg.read_inputs(tmp_path)[::2])["101990961244800001"]
    warm, _ = generator({}, chain=("models/a",))
    warm.force = False
    warm.sentences(bucket, "maize", "en", facts, [])  # caches maize/en from models/a
    stopped = cg.Generator(["cache"], None, None)
    stopped.stopped = "cache only (no calls)"
    output, summary = cg.build(deploy=tmp_path, facts_path=facts_path, generator=stopped, log=lambda *_: None)
    maize_en = output["stations"]["101990961244800001"]["crops"]["maize"]["en"]
    assert maize_en["source"] == "ai" and maize_en["model"] == "models/a"
    assert "about 8 mm" in maize_en["sentences"][0]["text"]
    assert summary["counts"] == {"ai": 2, "template": 22}  # maize/en for both stations, no calls made


def test_model_chain_keeps_only_listed_text_models():
    names = ["models/gemini-3.5-flash-lite", "models/gemini-3.7-flash", "models/gemini-3.8-flash-tts",
             "models/gemini-2.5-flash"]
    assert cg.model_chain("models/gemini-3.5-flash-lite", names) == [
        "models/gemini-3.5-flash-lite", "models/gemini-3.7-flash", "models/gemini-2.5-flash"]


# ----------------------------------------------------------------------------- built file
@pytest.mark.skipif(not cg.OUTPUT.is_file(), reason="web/data/crop_guide.json not built")
def test_built_file_is_safe():
    text = cg.OUTPUT.read_text(encoding="utf-8")
    output = json.loads(text)
    assert "AIza" not in text and not re.search(r"[A-Za-z0-9_\-]{35,}", text)
    for station in output["stations"].values():
        for crop, languages in station["crops"].items():
            for language, entry in languages.items():
                assert entry["source"] in ("ai", "template") and len(entry["sentences"]) == 3
                body = " ".join(sentence["text"] for sentence in entry["sentences"])
                assert not DISCLAIMER.search(body), f"{crop}/{language}"
                assert not LABEL_LEAKS.search(body), f"{crop}/{language}: {body}"
                assert (entry["model"] is None) == (entry["source"] == "template")
