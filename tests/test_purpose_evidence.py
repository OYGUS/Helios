"""Tests for connectors.purpose_evidence: evidence order, comment-noise filtering and
evidence strength (and the Ollama purpose gate built on it). No real HTTP."""
from __future__ import annotations

import json
import os
import textwrap
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from connectors import codebase_importer as ci  # noqa: E402
from connectors import ollama_enhancer as oe  # noqa: E402
from connectors import purpose_evidence as pe  # noqa: E402
from connectors.codebase_importer import SESSION_EVIDENCE_LABEL, build_code_map  # noqa: E402
from core.models import SolarSystem  # noqa: E402

# --- invented evidence shaped like typical scans -------------------------------------
VOICE_EV = "\n".join([
    "Header comment (asistan.py): Windows konsolunda Türkçe harflerin bozulmaması için UTF-8 ayarı",
    "Header comment (degerler.py): NOVA ORTAK YAPILANDIRMA DOSYASI İstenen ses, mikrofon ve model seçeneklerinin "
    "hepsi bu dosyada saklanır.",
    "Header comment (islemler.py): Kullanıcının komutla açıp kapattığı odak modu bilgisi",
    'Text in code (degerler.py, KARSILAMA_METNI): "Merhaba, ben NOVA. Bu bilgisayarda çevrimdışı çalışan, '
    'sakin ve yardımcı bir sesli asistanım. Yanıtlarımı Türkçe veririm."',
    "Capabilities implied by libraries: speech-to-text (faster_whisper), LLM / AI model (ollama)",
    "Project shape: Python, 4 code files",
    "Names in code: girdi_oku(), yanit_uret(), tarayici_ac()",
    f'{SESSION_EVIDENCE_LABEL}: "en son nerede bıraktık" | "kaldığın yerden devam et" | '
    '"şu an sesli konuşamıyorum"',
])
QUIZ_EV = "\n".join([
    f'{SESSION_EVIDENCE_LABEL}: "iki sürüm var hangisi daha iyi bakar mısın kıtapistan var bi de kıtapistan main var" | '
    '"sunucuyu başlat"',
    "README (README.md) is the unmodified create-next-app template (ignored).",
    'Header comment (next-env.d.ts): <reference types="next" <reference types="next/image-types/global"',
    "Capabilities implied by libraries: database (@prisma/client), web frontend (react, react-dom, next), 3D graphics (three)",
    "Project shape: TypeScript (React) + TypeScript, 125 code files",
    "Names in code: middleware(), NoticeKind (type), Exam (interface), StoredExam (interface)",
])
DICE_EV = "\n".join([
    f'{SESSION_EVIDENCE_LABEL}: "oyuncular bağlantı kurmaya uğraştı ama sunucu adresi açılmadı" | '
    '"zaten benzer bir taslak var ama gizli https://example.com/dice-server/draft" | '
    '"zar düellosu lobi açma kısmının botu nedir"',
    "Capabilities implied by libraries: web server / API (express), real-time communication (socket.io), database (mysql2)",
    "Project shape: JavaScript, 2 code files; web server / API (express)",
    "Entry point: server.js (is run by `scripts.start` (`node server.js`))",
    "Names in code: rollDice(), startRound(), endRound(), openLobby()",
])


def write(root: Path, rel: str, text: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(text), encoding="utf-8")
    return p


@pytest.fixture(autouse=True)
def _fake_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


# --- order ----------------------------------------------------------------------------
def test_compose_puts_session_line_first_and_keeps_its_budget():
    session = f'{SESSION_EVIDENCE_LABEL}: "' + "word " * 400 + '"'
    items = ["Own description (package.json): " + "x" * 3000, "Project shape: Python, 1 code file", "Names in code: a()"]
    ev = ci.compose_purpose_evidence(items, session)
    lines = ev.splitlines()
    assert lines[0].startswith(SESSION_EVIDENCE_LABEL)
    assert len(lines[0]) <= ci.PURPOSE_SESSION_RESERVE
    assert lines[-2:] == ["Project shape: Python, 1 code file", "Names in code: a()"]
    assert lines[1].startswith("Own description") and len(ev) <= ci.PURPOSE_EVIDENCE_BUDGET


def test_recompose_of_old_stored_order_moves_session_line_to_top():
    old = "Own description (package.json): A tool.\nProject shape: JS\n" + f'{SESSION_EVIDENCE_LABEL}: "make a tool"'
    ev = ci.compose_purpose_evidence(old.splitlines())
    assert ev.splitlines()[0] == f'{SESSION_EVIDENCE_LABEL}: "make a tool"'
    assert ev.count(SESSION_EVIDENCE_LABEL) == 1


def test_merge_purpose_keeps_old_session_line_first_even_from_old_order():
    existing = SolarSystem(name="P")
    existing.purpose_evidence = "Header comment (a.py): x\nProject shape: Python\n" + f'{SESSION_EVIDENCE_LABEL}: "build a bot"'
    fresh = SolarSystem(name="P")
    fresh.purpose_evidence = "Own description (pyproject.toml): A chat bot for a small club.\nProject shape: Python"
    ci.merge_purpose(existing, fresh)
    lines = existing.purpose_evidence.splitlines()
    assert lines[0] == f'{SESSION_EVIDENCE_LABEL}: "build a bot"'
    assert lines[1].startswith("Own description") and lines[-1] == "Project shape: Python"


def test_readers_accept_old_order():
    old = ("Own description (package.json): A tool.\nCapabilities implied by libraries: HTTP client (requests)\n"
           f'{SESSION_EVIDENCE_LABEL}: "make a tool that renames photos by date"')
    new = ci.compose_purpose_evidence(old.splitlines())
    for ev in (old, new):
        assert ci._purpose_origin(ev) == "the project's own description in package.json"
        assert ci._evidence_capabilities(ev) == "HTTP client (requests)"
        assert pe.session_requests(pe.session_line_of(ev)) == ["make a tool that renames photos by date"]
    assert pe.assess_purpose_evidence(old).score == pe.assess_purpose_evidence(new).score == 3


# --- noise filter ---------------------------------------------------------------------
@pytest.mark.parametrize("line,category", [
    ("# -*- coding: utf-8 -*-", "encoding"),
    ("# coding=utf-8", "encoding"),
    ("# Windows konsolunda Türkçe harflerin bozulmaması için UTF-8 ayarı", "encoding"),
    ("// Set console encoding for the terminal", "encoding"),
    ("#!/usr/bin/env python3", "shebang"),
    ("// SPDX-License-Identifier: MIT", "license"),
    ("Copyright (c) 2024 Someone", "license"),
    ("© 2023 ACME", "license"),
    ("Licensed under the Apache License, Version 2.0", "license"),
    ("MIT License", "license"),
    ("All rights reserved.", "license"),
    ("Permission is hereby granted, free of charge, to any person", "license"),
    ("See https://opensource.org/licenses/MIT", "license"),
    ("Bu yazılım MIT lisansı ile dağıtılır", "license"),
    ("Tüm hakları saklıdır.", "license"),
    ("/* eslint-disable no-console */", "directive"),
    ("# pylint: disable=invalid-name", "directive"),
    ("# noqa: E501", "directive"),
    ("# type: ignore", "directive"),
    ("# mypy: ignore-errors", "directive"),
    ("// @ts-nocheck", "directive"),
    ("// prettier-ignore", "directive"),
    ("/* jshint esversion: 6 */", "directive"),
    ("// phpcs:ignoreFile", "directive"),
    ("/* istanbul ignore next */", "directive"),
    ("'use strict';", "directive"),
    ("/** @flow */", "directive"),
    ("/// <reference types=\"next\" />", "reference"),
    ("<reference types=\"next/image-types/global\"", "reference"),
    ("# vim: set ts=4 sw=4 :", "modeline"),
    ("This file was generated by protoc. DO NOT EDIT!", "generated"),
    ("// Code auto-generated by the build", "generated"),
    ("DO NOT MODIFY", "generated"),
    ("NOTE: This file should not be edited", "generated"),
    ("Bu dosya otomatik oluşturuldu, elle düzenlemeyin", "generated"),
    ("Author: Jane Doe", "metadata"),
    (" * @author Jane", "metadata"),
    ("Created on Mon Jan 1 2024", "metadata"),
    ("Date: 2024-01-01", "metadata"),
    ("@version 1.2", "metadata"),
    ("@file main.js", "metadata"),
    ("@package App", "metadata"),
    ("Version: 2.0", "metadata"),
    ("# ==========", "separator"),
    ("// ---------------", "separator"),
    ("#####", "separator"),
    (" ****", "separator"),
])
def test_noise_categories(line, category):
    assert pe.noise_category(line) == category
    assert pe.is_noise_comment_line(line)


@pytest.mark.parametrize("line", [
    "NOVA ORTAK YAPILANDIRMA DOSYASI İstenen ses, mikrofon ve model seçeneklerinin hepsi bu dosyada saklanır.",
    "Kullanıcının komutla açıp kapattığı odak modu bilgisi",
    "A small web app that tracks the books a reading club has finished.",
    "Video encoding tool for short clips",
    "Global settings for the bot",
    "@fileoverview Renders the lobby list of the dice game.",
])
def test_descriptive_lines_are_kept(line):
    assert not pe.is_noise_comment_line(line)
    assert pe.clean_header_text(line)


def test_clean_header_text_line_by_line_and_minimum_words():
    text = "Copyright 2024 X\nSPDX-License-Identifier: MIT\nDice game server for Discord lobbies.\nAuthor: me"
    assert pe.clean_header_text(text) == "Dice game server for Discord lobbies."
    assert pe.clean_header_text("Copyright 2024 X\nMain file") == ""  # < 3 meaningful words left
    assert pe.clean_header_text("@fileoverview Renders the lobby list.") == "Renders the lobby list."


def test_python_header_falls_back_to_next_block():
    lines = textwrap.dedent("""\
        #!/usr/bin/env python3
        # -*- coding: utf-8 -*-
        # Windows konsolunda Türkçe harf sorunu yaşanmaması için UTF-8 ayarı
        import sys

        # Copyright (c) 2024 Someone
        # SPDX-License-Identifier: MIT

        # Sesli komutlarla ders notlarını okuyan yerel yardımcı
        # mikrofonu dinler ve notları sesli okur
        def main():
            pass
        """).splitlines()
    assert ci._first_comment_block(lines) == ("Sesli komutlarla ders notlarını okuyan yerel yardımcı "
                                              "mikrofonu dinler ve notları sesli okur")


def test_python_docstring_noise_paragraph_is_skipped():
    doc = "Copyright 2024 Someone. All rights reserved.\n\nTracks a reading club's finished books and votes."
    assert ci._docstring_description(doc) == "Tracks a reading club's finished books and votes."
    assert ci._docstring_description("Author: me\nVersion: 1.0") == ""


@pytest.mark.parametrize("source,expected", [
    ('/// <reference types="next" />\n/// <reference types="next/image-types/global" />\n\n'
     "// NOTE: This file should not be edited\n// see https://nextjs.org/docs for more information.\n", ""),
    ("/*!\n * SPDX-License-Identifier: MIT\n * Copyright (c) 2024 X\n */\n'use strict';\n"
     "// Lobby server for the dice game: rooms, turns and payouts\nconst x = 1;\n",
     "Lobby server for the dice game: rooms, turns and payouts"),
    ("<?php\n// Auto-generated by the installer. Do not edit.\n/**\n * TaleWeave - Shared story writing site index page\n"
     " * @author someone\n */\n$x = 1;\n", "TaleWeave - Shared story writing site index page"),
    ("/* eslint-disable */\n/* Theme colours and spacing for the quiz pages */\nbody {}\n",
     "Theme colours and spacing for the quiz pages"),
    ("<!-- Copyright 2024 X -->\n<!-- Landing page of the photo sharing club site -->\n<html></html>\n",
     "Landing page of the photo sharing club site"),
    ("const app = express();\n// set up the routes for the shop\n", ""),
])
def test_generic_header_noise_and_fallback(source, expected):
    assert ci._generic_header_comment(source) == expected


def test_scan_drops_noise_headers_and_session_line_first(tmp_path):
    root = tmp_path / "proj"
    write(root, "next-env.d.ts", '/// <reference types="next" />\n/// <reference types="next/image-types/global" />\n')
    write(root, "app.py", "# -*- coding: utf-8 -*-\n# Windows konsolunda UTF-8 ayarı\nimport os\n")
    write(root, "index.js", "// Copyright 2024 X\n// Quiz builder for bakery staff training\nconst a = 1;\n")
    system = SolarSystem(name="proj", project_name="proj", workspace_path=str(root))
    code = build_code_map(root, system)
    ev = ci.compose_purpose_evidence(code.purpose_items, f'{SESSION_EVIDENCE_LABEL}: "make the quiz builder work"')
    assert ev.splitlines()[0].startswith(SESSION_EVIDENCE_LABEL)
    assert "<reference" not in ev and "UTF-8" not in ev and "Copyright" not in ev
    assert "Quiz builder for bakery staff training" in ev


# --- strength -------------------------------------------------------------------------
def test_voice_assistant_is_strong():
    r = pe.assess_purpose_evidence(VOICE_EV, "NOVA")
    assert r.strong and r.score == 3 and r.needed == pe.PURPOSE_STRONG_SCORE == 2
    assert "header comment in asistan.py" not in r.signals  # the UTF-8 note is noise
    assert r.signals == ["header comment in degerler.py", "header comment in islemler.py", "text in degerler.py"]
    assert r.explanation.startswith("Strong evidence (3 points, 2 needed): header comment in degerler.py;")


def test_quiz_app_is_weak():
    r = pe.assess_purpose_evidence(QUIZ_EV, "KITAPİSTAN")
    assert not r.strong and r.score == 0 and r.signals == []
    assert r.explanation == ("Weak evidence (0 points, 2 needed): no description, header comment, "
                             "user-facing text or descriptive session request")


def test_dice_server_is_strong_only_from_two_session_requests():
    r = pe.assess_purpose_evidence(DICE_EV, "dice-server")
    assert r.strong and r.score == 2
    assert all(s.startswith("session request") for s in r.signals)
    assert not any("zaten" in s for s in r.signals)  # 3 meaningful words once the URL is removed


def test_scoring_rules():
    assert pe.assess_purpose_evidence("Own description (package.json): A tool.").score == 2
    assert pe.assess_purpose_evidence("README intro (README.md, titled \"X\"): Hello.").score == 2
    assert pe.assess_purpose_evidence("README title (README.md): Dice Game").score == 1
    facts = ("Capabilities implied by libraries: x\nProject shape: y\nEntry point: z\nNames in code: a(), b()\n"
             "README (README.md) is the unmodified Vite template (ignored).")
    assert pe.assess_purpose_evidence(facts).score == 0
    titles = ('Titles shown to users: page title "Photo club gallery" (index.html); heading "Upload your photos" '
              '(index.html); meta description "Share photos with club friends" (index.html); page title "Home" (a.html)')
    r = pe.assess_purpose_evidence(titles)
    assert r.score == 2 and r.signals == ["page title in index.html", "heading in index.html"]
    assert pe.assess_purpose_evidence('Command-line help: argparse prog "scanguard" (cli.py)', "scanguard").score == 0
    assert pe.assess_purpose_evidence('Text in code (a.php, q): "WHERE b.status != 1 AND EXISTS (SELECT 1 FROM c)"').score == 0
    assert pe.assess_purpose_evidence('Text in code (a.py, MSG): "Welcome to the photo club gallery app"').score == 1
    sessions = (f'{SESSION_EVIDENCE_LABEL}: "build a discord bot that rolls dice for lobbies" | '
                '"add a leaderboard showing weekly winners please" | "store every finished game result safely"')
    assert pe.assess_purpose_evidence(sessions).score == 2  # at most 2 from sessions
    assert pe.assess_purpose_evidence(f'{SESSION_EVIDENCE_LABEL}: "devam et" | "projeyi başlat bakar mısın"').score == 0
    assert pe.assess_purpose_evidence("").strong is False


def test_meaningful_words():
    words = pe.meaningful_words("Bu proje https://x.io/a için src/app/main.py 2024 ile çalışır ve KITAPİSTAN", ["kıtapistan"])
    assert words == ["çalışır"]


# --- the Ollama gate --------------------------------------------------------------------
class _FakeResponse:
    def __init__(self, body: bytes):
        self._body = body
        self.status = 200

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _CountingOpener:
    def __init__(self, answer: str):
        self.answer, self.requests = answer, []

    def open(self, req, timeout=None):
        self.requests.append(req)
        return _FakeResponse(json.dumps({"response": self.answer}).encode("utf-8"))


def test_describe_purpose_weak_makes_no_request(monkeypatch):
    opener = _CountingOpener("A bakery quiz app.")
    monkeypatch.setattr(oe, "_DIRECT_OPENER", opener)
    ok, msg, text = oe.describe_purpose("KITAPİSTAN", QUIZ_EV, "m")
    assert ok is False and text is None and opener.requests == []
    assert "Weak evidence (0 points, 2 needed)" in msg


def test_describe_purpose_strong_is_sent(monkeypatch):
    opener = _CountingOpener("A local voice assistant that runs PC commands.")
    monkeypatch.setattr(oe, "_DIRECT_OPENER", opener)
    ok, _msg, text = oe.describe_purpose("NOVA", VOICE_EV, "m")
    assert ok and text.startswith("A local voice assistant") and len(opener.requests) == 1
    prompt = json.loads(opener.requests[0].data.decode("utf-8"))["prompt"]
    evidence = prompt.split("EVIDENCE:\n", 1)[1]
    assert evidence.startswith(SESSION_EVIDENCE_LABEL)  # VOICE_EV is in the old order; the prompt reorders it


@pytest.fixture(scope="module")
def qapp():
    from PySide6 import QtWidgets

    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _wait(predicate, timeout=10.0):
    from PySide6 import QtCore

    app = QtCore.QCoreApplication.instance()
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "timed out"
        app.processEvents(QtCore.QEventLoop.ProcessEventsFlag.AllEvents, 20)
        time.sleep(0.005)


def test_worker_skips_weak_and_asks_for_strong(qapp, monkeypatch):
    weak = SolarSystem(name="KITAPİSTAN", project_name="KITAPİSTAN")
    weak.purpose_evidence = QUIZ_EV
    strong = SolarSystem(name="NOVA", project_name="NOVA")
    strong.purpose_evidence = VOICE_EV
    opener = _CountingOpener("A local voice assistant.")
    monkeypatch.setattr(oe, "_DIRECT_OPENER", opener)
    # Changed: the manual flag (purpose_manual) replaces `force` for the purpose step.
    for manual in (False, True):
        opener.requests.clear()
        worker = oe.OllamaEnhanceWorker([weak, strong], model_name="m:1", purpose_only=True, purpose_manual=manual)
        assert worker.purpose_needed(worker.snapshots[0]) is False
        assert worker.purpose_needed(worker.snapshots[1]) is True
        written, finished = [], []
        worker.purposeSuggested.connect(lambda sid, text: written.append(sid))
        worker.finishedEnhance.connect(lambda ok, msg: finished.append((ok, msg)))
        worker.start()
        _wait(lambda: bool(finished) and worker.isFinished())
        assert written == [strong.id] and len(opener.requests) == 1
        assert "1 suggested, 1 skipped, 0 failed" in finished[0][1]
