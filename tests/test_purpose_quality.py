"""Tests for connectors/purpose_quality.py (quality check of a model-written purpose) and
its use in `ollama_enhancer.describe_purpose`.

Pure: no Ollama server and no real HTTP (the module-level `_DIRECT_OPENER` is replaced by
a fake). The realistic evidence texts below are invented, shaped like real galaxy.json entries.
"""

from __future__ import annotations

import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from connectors import ollama_enhancer as oe  # noqa: E402
from connectors.purpose_evidence import SESSION_EVIDENCE_LABEL  # noqa: E402
from connectors.purpose_quality import (  # noqa: E402
    REASONS,
    QualityResult,
    check_purpose_suggestion,
    count_sentences,
    evidence_text,
    evidence_words,
    is_unknown,
    name_keys,
    words_match,
)

EV = "Own description (package.json): A small tool that renames holiday photos by date.\nNames in code: computeDiceTotal(), odak_modu()"
TR_EV = "Header comment (quiz.py): Türkçe kelime quizleri hazırlayan öğrenme aracı\nNames in code: soruSor()"


def _reason(text, evidence=EV, name="P"):
    return check_purpose_suggestion(text, evidence, name).reason


# ----------------------------------------------------------------------
# Reject reasons
# ----------------------------------------------------------------------
@pytest.mark.parametrize("text", ["", "   ", "...!?", "**", "— • —"])
def test_empty(text):
    res = check_purpose_suggestion(text, EV, "P")
    assert res == QualityResult(False, "empty", REASONS["empty"])


@pytest.mark.parametrize("text", [
    "UNKNOWN", "unknown.", "**UNKNOWN**", "Unknown - too little evidence",
    "I don't know.", "I do not know what this project does.",
    "Not enough information to tell.", "There is not enough evidence.",
    "Based on the evidence, I cannot determine what it is for.", "Cannot determine the purpose.",
    "It is unclear what this project is for.", "The evidence does not say what this is.",
    "Sorry, I am unable to tell.",
    "Bilinmiyor.", "BİLİNMİYOR", "Yeterli bilgi yok.", "Kanıtlara göre belirlenemiyor.",
])
def test_unknown(text):
    assert _reason(text, TR_EV) == "unknown"
    assert is_unknown(text)


def test_unknown_word_later_in_a_real_answer_is_not_unknown():
    assert not is_unknown("A holiday photo renamer. The order of unknown files is kept.")


@pytest.mark.parametrize("text", [
    "It renames photos. It sorts them. It is fast.",
    "A renamer.\nIt renames photos.\nIt sorts holiday pictures.",
    "Holiday photos! Renamed? Sorted.",
    "Tatil fotoğraflarını yeniden adlandırır. Tarihe göre sıralar. Hızlıdır.",
    "photo " * 90,  # one "sentence" over 400 characters
])
def test_too_long(text):
    assert _reason(text) == "too_long"


def test_sentence_count_ignores_abbreviations_decimals_versions_files_and_urls():
    text = ("It renames photos, e.g. holiday ones, i.e. JPEGs, etc. and sorts them vs. by date. "
            "It reads main.py and config.json, needs v1.2.3 or 2.0, costs 1.5 dollars, see https://x.org/a.b.html, örn. bkz. vb. şeyler.")
    assert count_sentences(text) == 2
    assert check_purpose_suggestion(text, EV, "P").ok
    assert count_sentences("One. Two! Three?") == 3
    assert count_sentences("No end mark") == 1


@pytest.mark.parametrize("text,name", [
    ("NOVA is the NOVA project.", "NOVA"),
    ("NOVA'nın projesi NOVA.", "NOVA"),
    ("PhotoTool is a tool.", "PhotoTool"),
    ("dice-server is the dice server application.", "dice-server"),
    ("KITAPİSTAN bir uygulamadır.", "KITAPİSTAN"),
])
def test_name_only(text, name):
    assert _reason(text, EV, name) == "name_only"


@pytest.mark.parametrize("text", [
    "A software project that helps users manage data.",
    "This is an application that provides various features.",
    "This is a tool that allows users to manage tasks and files.",
    "A project for photos.",
    "Based on the evidence, this is likely a software tool.",
    "The evidence shows a program that is used for something.",
    "This program appears to provide functionality for users.",
    "Kullanıcılar için çeşitli özellikler sağlayan bir yazılım uygulaması.",
    "Muhtemelen veri yönetimi için bir araç.",
])
def test_generic(text):
    assert _reason(text, TR_EV) == "generic"


@pytest.mark.parametrize("text,evidence", [
    ("A chess engine that plays tournaments online.", EV),
    ("A bakery shop with online payments.", EV),
    ("Bir müzik çalar ve radyo dinleme programı.", TR_EV),
    # Words that only appear in labels, file names or importer boilerplate do not count.
    ("Parses package json headers and comments.", EV),
    ("Reads the package description and the quiz header.", "Own description (package.json): renames holiday photos\n"),
])
def test_no_overlap(text, evidence):
    assert _reason(text, evidence) == "no_overlap"


def test_reason_order():
    # too_long beats generic / no_overlap; name_only beats generic.
    assert _reason("A tool. A program. An app.") == "too_long"
    assert _reason("UNKNOWN. UNKNOWN. UNKNOWN.") == "unknown"
    assert _reason("Photon is the Photon app.", EV, "Photon") == "name_only"


def test_detail_is_short_english():
    res = check_purpose_suggestion("A chess engine for tournaments.", EV, "P")
    assert res.detail == "the answer did not match the evidence"
    assert set(REASONS) == {"empty", "unknown", "too_long", "name_only", "generic", "no_overlap"}


# ----------------------------------------------------------------------
# Accept cases and matching
# ----------------------------------------------------------------------
@pytest.mark.parametrize("text,evidence,expected", [
    ("A holiday photo renamer.", EV, {"holiday", "photo", "renamer"}),
    ("A dice game played in rounds.", EV, {"dice"}),
    ("A Turkish vocabulary quiz maker.", TR_EV, {"Turkish", "quiz"}),
    ("Türkçe kelime quiz uygulaması.", TR_EV, {"Türkçe", "kelime", "quiz"}),
    ("A TURKISH QUIZ ABOUT İSTANBUL.", TR_EV, {"TURKISH", "QUIZ"}),
    ("Sure, it is a **holiday photo** renamer.", EV, {"holiday", "photo", "renamer"}),
])
def test_accept(text, evidence, expected):
    res = check_purpose_suggestion(text, evidence, "P")
    assert res.ok and res.reason == "" and res.detail == ""
    assert {m.split("~")[0] for m in res.matched} == expected


def test_words_match_rules():
    from connectors.purpose_evidence import word_key as k
    assert words_match(k("Turkish"), k("Türkçe"))
    assert words_match(k("quiz"), k("Quizzes"))
    assert words_match(k("game"), k("games"))
    assert words_match(k("assistant"), k("asistanısın"))  # doubled letters count once
    assert words_match(k("İstanbul"), k("istanbul")) and words_match(k("IŞIK"), k("ışık"))
    assert not words_match(k("chess"), k("check"))
    assert not words_match(k("rocket"), k("roket"))


def test_evidence_side_drops_labels_names_files_and_boilerplate():
    ev = "\n".join([
        f'{SESSION_EVIDENCE_LABEL}: "odak modunu aç"',
        "Header comment (settings_loader.py): Holiday Photos settings",
        "Entry point: server.js (is run by `scripts.start` (`node server.js`))",
        "Project shape: JavaScript, 2 code files",
        "Names in code: computeDiceTotal(), NoticeKind (type), TaleWeave (class)",
        "README (README.md) is the unmodified create-next-app template (ignored).",
    ])
    text = evidence_text(ev)
    assert "Header comment" not in text and "settings_loader" not in text and "scripts" not in text
    words = {w.lower() for w, _ in evidence_words(ev, "TaleWeave")}
    assert {"compute", "dice", "total", "notice", "holiday", "photos", "odak", "server"} <= words
    for w in ("first", "sessions", "asked", "header", "comment", "names", "code", "files", "javascript",
              "type", "class", "tale", "weave", "taleweave", "readme", "template", "node", "start"):
        assert w not in words, w


def test_name_keys_split_parts():
    assert name_keys("dice-server") == {"dice", "server", "diceserver"}
    assert {"taleweave", "tale", "weave"} <= name_keys("TaleWeave")
    assert name_keys("KITAPİSTAN") == {"kitapistan"}


# ----------------------------------------------------------------------
# Realistic answers with realistic evidence (invented, shaped like galaxy.json entries)
# ----------------------------------------------------------------------
VOICE_EV = "\n".join([
    "Header comment (asistan.py): Windows konsolunda Türkçe harflerin bozulmaması için UTF-8 ayarı",
    "Header comment (degerler.py): NOVA ORTAK YAPILANDIRMA DOSYASI İstenen ses, mikrofon ve model seçeneklerinin hepsi bu dosyada saklanır.",
    "Header comment (islemler.py): Kullanıcının komutla açıp kapattığı odak modu bilgisi",
    'Text in code (degerler.py, KARSILAMA_METNI): "Merhaba, ben NOVA. Bu bilgisayarda çevrimdışı çalışan, sakin ve yardımcı bir sesli asistanım. Yanıtlarımı Türkçe veririm. Kısa ve sade konuşurum. İZİN VERİLEN EYLEMLER (İŞLEMLER): Tarayıcıyı aç…"',
    "Text in code (degerler.py, SESLI_KARSILAMA): \"NOVA dinlemeye hazır! ([{…}] tuşuna basılı tutup konuşun, kapatmak için [ESC] tuşuna basın)\"",
    'Text in code (degerler.py, IZINSIZ_EYLEM_METNI): "Bu isteği yerine getiremem, yalnızca izin verilen eylemleri yapabilirim."',
    "Capabilities implied by libraries: speech-to-text (faster_whisper), microphone/audio input (sounddevice), LLM / AI model (ollama), keyboard/mouse automation (keyboard), system/process monitoring (psutil), data processing / spreadsheets (numpy)",
    "Project shape: Python, 4 code files",
    'Entry point: asistan.py (has an `if __name__ == "__main__"` block and is not imported by other project files)',
    "Names in code: girdi_oku(), yanit_uret(), baslat(), tarayici_ac(), saati_goster(), bildirim_kapat(), odak_acik_mi(), odak_modu_aktif_mi(), odak_modu(), gorev_yurut(), ses_kaydet()",
    f'{SESSION_EVIDENCE_LABEL}: "en son nerede bıraktık" | "kaldığın yerden devam et" | "şu an sesli konuşamıyorum" | "ikisini birden yapsak sence hangisi önce gelir"',
])
VOICE_ANSWER = (
    "A local voice assistant that reads notes aloud and answers short questions, mostly in Turkish. "
    "It reacts to spoken requests, opens a few allowed tools and keeps a quiet focus timer, while also "
    "reporting where its own work was left."
)

DICE_EV = "\n".join([
    "Capabilities implied by libraries: web server / API (express), real-time communication (socket.io), database (mysql2)",
    "Project shape: JavaScript, 2 code files; web server / API (express)",
    "Entry point: server.js (is run by `scripts.start` (`node server.js`))",
    "Names in code: rollDice(), startGame(), endGame(), recordGameResult(), handlePayout(), openLobby(), getNextTurnIndex(), finishGame(), broadcastLobbies(), getLobbiesList()",
    f'{SESSION_EVIDENCE_LABEL}: "oyuncu bir adresle bağlanmaya uğraştı ama adres açılmadı" | "zaten benzer bir taslak var ama private https://example.com/dice-server/draft" | "zar düellosu lobi açma kısmının botu nedir"',
])
DICE_ANSWER = (
    "This project appears to be a web-based game, likely a kind of luck contest, that lets users join and compete "
    "through a server. Users were trying to join using addresses and mentioned a private code repository, "
    "suggesting a limited access game experience."
)

TALE_EV = "\n".join([
    "Header comment (index.php): TaleWeave - Home Page",
    "Header comment (config/config.php): TaleWeave - Settings File",
    "Header comment (home.php): TaleWeave - Member Dashboard",
    'Titles shown to users: meta description "TaleWeave - Collaborative storytelling platform. Write stories together, branch your creative ideas." (index.php); heading "Weave Stories Worlds Together" (index.php); page title "- TaleWeave" (home.php); heading "Tale Weave —" (home.php)',
    'Text in code (index.php, pageTitle): "TaleWeave - Stories Written Together"',
    'Text in code (oauth.php, welcomeMsg): "Welcome to TaleWeave,"',
    "Text in code (threads.php, searchCondition): \"WHERE s.state != 'draft' AND EXISTS (SELECT 1 FROM parts p WHERE p.story_id = s.id AND p.state = 'live') AND s.title LIKE ?\"",
    "Capabilities implied by libraries: database (PDO in PHP)",
    "Project shape: PHP + JavaScript, 73 code files; PHP website: index.php plus 23 other pages at the root (admin, chapter, library, shelf, reset-password, threads, oauth, profile +15 more)",
    "Entry point: index.php (is `index.php` at the project root (the page the web server opens first))",
    "Names in code: loadEnv(), Db (class), oauthRequest(), makeUniqueHandle(), finishOauthLogin(), logModeratorAction(), shortCount(), timeAgo(), renderThread(), refreshTagCounts(), escape(), cleanHtml()",
    f'{SESSION_EVIDENCE_LABEL}: "sitenin adını değiştirmek istiyorum bir isim önerir misin" | "ortak yazmayı ya da dallanan bölümleri anlatan bir isim lazım" | ".net alan adı boş olsun" | "isim kalsın şimdi siteye bak bozuk bir bölüm var mı hem görünüm hem özellik olarak…"',
])
TALE_ANSWER = (
    "A collaborative storytelling platform where members write stories together and continue each other's parts. "
    "It supports branching plots, a discussion forum and profiles, and gives creative writers a shared space."
)

QUIZ_EV = "\n".join([
    "README (README.md) is the unmodified create-next-app template (ignored).",
    'Header comment (next-env.d.ts): <reference types="next" <reference types="next/image-types/global"',
    "Capabilities implied by libraries: database (@prisma/client, prisma, @supabase/supabase-js), web frontend (react, react-dom, next), 3D graphics (three)",
    "Project shape: TypeScript (React) + TypeScript, 125 code files",
    "Names in code: middleware(), NoticeKind (type), Notice (interface), NoticeContextProps (interface), useNotice(), NoticeProvider(), staffGuard(), isStaff(), Choice (interface), Question (interface), Quiz (interface), StoredQuiz (interface)",
    f'{SESSION_EVIDENCE_LABEL}: "iki sürüm var hangisi daha iyi bakar mısın kıtapistan var bi de kıtapistan main var" | "sunucuyu başlat"',
])
QUIZ_ANSWER = (
    "This project is a system for creating quizzes and potentially managing data, likely related to gardening or "
    "similar hobbies, given the names used. It appears to be designed to launch and run a specific file named "
    "\"kıtapistan main\"."
)


def _matched(res):
    return {m.split("~")[0].lower() for m in res.matched}


def test_voice_assistant_answer_is_ok():
    res = check_purpose_suggestion(VOICE_ANSWER, VOICE_EV, "NOVA")
    assert res.ok
    assert _matched(res) == {"turkish", "assistant"}  # Türkçe, asistanım


def test_dice_server_answer_is_ok():
    res = check_purpose_suggestion(DICE_ANSWER, DICE_EV, "dice-server")
    assert res.ok
    assert _matched(res) == {"web", "game", "private", "addresses"}  # "server" is a name word
    assert "server" not in _matched(res)


def test_story_site_answer_is_ok():
    res = check_purpose_suggestion(TALE_ANSWER, TALE_EV, "TALEWEAVE")
    assert res.ok
    assert {"collaborative", "storytelling", "stories", "together", "branching", "creative"} <= _matched(res)


def test_quiz_app_answer_passes_only_through_quiz():
    # The answer invents "gardening"; only "quizzes" ~ "Quiz" overlaps. The weak-evidence
    # gate (purpose_evidence.assess_purpose_evidence) keeps this project from being asked.
    res = check_purpose_suggestion(QUIZ_ANSWER, QUIZ_EV, "KITAPİSTAN")
    assert res.ok and _matched(res) == {"quizzes"}
    from connectors.purpose_evidence import assess_purpose_evidence
    assert not assess_purpose_evidence(QUIZ_EV, "KITAPİSTAN").strong


# ----------------------------------------------------------------------
# describe_purpose
# ----------------------------------------------------------------------
class _FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeOpener:
    def __init__(self, answer: str):
        self.answer, self.requests = answer, []

    def open(self, req, timeout=None):
        self.requests.append(req)
        return _FakeResponse(json.dumps({"response": self.answer}).encode("utf-8"))


def _describe(monkeypatch, answer, name="NOVA", evidence=VOICE_EV):
    monkeypatch.setattr(oe, "_DIRECT_OPENER", _FakeOpener(answer))
    return oe.describe_purpose(name, evidence, "m:1", "English")


def test_describe_purpose_rejects_three_sentences_as_too_long(monkeypatch):
    ok, msg, text = _describe(monkeypatch, "Here is the purpose: A local voice assistant. It speaks Turkish. It opens games.")
    assert ok is False and text is None
    assert oe.purpose_rejection_reason(msg) == "too_long"
    assert msg == ("No suggestion for 'NOVA': the answer was longer than 2 sentences / 400 characters "
                   "(reason: too_long).")


def test_describe_purpose_rejects_unknown_and_other_reasons(monkeypatch):
    for answer, reason in (
        ("**UNKNOWN**", "unknown"),
        ("I don't know.", "unknown"),
        ("NOVA is the NOVA project.", "name_only"),
        ("A software project that helps users manage data.", "generic"),
        ("A chess engine for online tournaments.", "no_overlap"),
        ("YOUR TASK: say what this project is and what it is used for", "echo"),
    ):
        ok, msg, text = _describe(monkeypatch, answer)
        assert ok is False and text is None, answer
        assert oe.purpose_rejection_reason(msg) == reason, (answer, msg)
    assert "UNKNOWN" in _describe(monkeypatch, "UNKNOWN")[1]


def test_describe_purpose_good_answer(monkeypatch):
    ok, msg, text = _describe(monkeypatch, "Sure! Here it is:\n**A local voice assistant** that answers in Turkish.")
    assert ok and text == "A local voice assistant that answers in Turkish."
    assert oe.purpose_rejection_reason(msg) == "" and "NOVA" in msg


def test_purpose_prompt_asks_for_domain_words_no_hedging_and_unknown():
    prompt = oe.build_purpose_prompt("NOVA", VOICE_EV, "Türkçe")
    for phrase in ("at most 2 short sentences", "key domain words", "No hedging words",
                   "do not just restate the project name", "guess from names alone",
                   "answer exactly: UNKNOWN", "Write in Türkçe."):
        assert phrase in prompt, phrase
    assert len(oe.build_purpose_prompt("X" * 500, "evidence line\n" * 2000, "English")) <= oe.MAX_PURPOSE_PROMPT_CHARS
