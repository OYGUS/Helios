"""Local Ollama LLM Verification and Project Summary Enhancer.

Core Principle:
- Helios extracts each project's purpose, architecture, and modules independently
  using its deterministic codebase and session analysis engine without requiring Ollama.
- This module uses a locally installed Ollama model (e.g., `gemma3:4b`) as an optional
  verifier and enhancer: it inspects the project's actual file structure and modules
  to refine and enrich the Project Core (Sun) summary.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import List, Optional, Tuple

from PySide6.QtCore import QThread, Signal

from core.models import SolarSystem

OLLAMA_BASE_URL = "http://127.0.0.1:11434"
OLLAMA_MARKER = "OLLAMA VERIFIED ARCHITECTURE ANALYSIS"

# Bypass system proxy/VPN settings for local 127.0.0.1 requests
_DIRECT_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class OllamaEnhancer:
    """Helper class that communicates with the local Ollama service and enhances project summaries."""

    @staticmethod
    def is_server_running(timeout: float = 1.5) -> bool:
        """Checks whether the Ollama HTTP API service is running."""
        try:
            req = urllib.request.Request(f"{OLLAMA_BASE_URL}/api/tags", method="GET")
            with _DIRECT_OPENER.open(req, timeout=timeout) as resp:
                return resp.status == 200
        except Exception:
            return False

    @staticmethod
    def detect_installed_models() -> List[str]:
        """Detects installed models from both the active Ollama API and ~/.ollama/models."""
        models: List[str] = []

        # 1. Read from the live API if running
        try:
            req = urllib.request.Request(f"{OLLAMA_BASE_URL}/api/tags", method="GET")
            with _DIRECT_OPENER.open(req, timeout=1.5) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
                for item in data.get("models", []):
                    name = str(item.get("name", "")).strip()
                    if name and name not in models:
                        models.append(name)
        except Exception:
            pass

        # 2. Scan ~/.ollama/models/manifests on disk (discovers models even when server is stopped)
        manifests_root = (
            Path.home() / ".ollama" / "models" / "manifests" / "registry.ollama.ai" / "library"
        )
        if manifests_root.exists():
            try:
                for model_dir in manifests_root.iterdir():
                    if model_dir.is_dir():
                        for tag_file in model_dir.iterdir():
                            if tag_file.is_file():
                                candidate = f"{model_dir.name}:{tag_file.name}"
                                if candidate not in models:
                                    models.append(candidate)
            except Exception:
                pass

        return models

    @staticmethod
    def get_preferred_model(chosen_model: Optional[str] = None) -> Optional[str]:
        """Returns the requested model or the best available installed model."""
        models = OllamaEnhancer.detect_installed_models()
        if not models:
            return None
        if chosen_model:
            for m in models:
                if m.lower() == chosen_model.lower() or chosen_model.lower() in m.lower():
                    return m
        # Priority order: Qwen or Gemma
        for m in models:
            if "qwen2.5" in m.lower():
                return m
        for m in models:
            if "gemma3" in m.lower():
                return m
        return models[0]

    @staticmethod
    def ensure_server_running() -> bool:
        """Silently starts the Ollama service in the background if it is not running."""
        if OllamaEnhancer.is_server_running(timeout=1.0):
            return True

        try:
            creation_flags = 0
            if sys.platform.startswith("win"):
                creation_flags = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
            env = os.environ.copy()
            env["OLLAMA_VULKAN"] = "0"
            env["OLLAMA_NOPRUNE"] = "1"
            subprocess.Popen(
                ["ollama", "serve"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
                creationflags=creation_flags,
            )
        except Exception:
            return False

        for _ in range(32):
            time.sleep(0.5)
            if OllamaEnhancer.is_server_running(timeout=1.2):
                return True
        return False

    @staticmethod
    def _collect_workspace_evidence(system: SolarSystem) -> str:
        """Collects file and structural evidence from the project's workspace directory if present."""
        ws_str = system.workspace_path.strip()
        if not ws_str:
            return ""
        ws = Path(ws_str)
        if not ws.exists() or not ws.is_dir():
            return ""

        evidence_lines: List[str] = [f"Workspace Directory: {ws}"]

        # Important configuration and documentation files
        for cfg_name in ("README.md", "AGENTS.md", "package.json", "requirements.txt", "pyproject.toml"):
            cfg_path = ws / cfg_name
            if cfg_path.exists() and cfg_path.is_file():
                try:
                    raw = cfg_path.read_text(encoding="utf-8", errors="replace").strip()
                    evidence_lines.append(f"--- {cfg_name} ---\n{raw[:600]}")
                except Exception:
                    pass

        # Main source files in the root folder
        try:
            code_files = [
                p.name
                for p in ws.iterdir()
                if p.is_file()
                and p.suffix.lower() in {".py", ".ts", ".tsx", ".js", ".php", ".sql", ".json", ".cmd", ".bat"}
            ][:18]
            if code_files:
                evidence_lines.append("Root Directory Files: " + ", ".join(code_files))
        except Exception:
            pass

        return "\n".join(evidence_lines)

    @staticmethod
    def enhance_system(
        system: SolarSystem,
        model_name: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """Verifies and enriches the core plan of a project (SolarSystem) using an Ollama model."""
        chosen_model = model_name or OllamaEnhancer.get_preferred_model()
        if not chosen_model:
            return False, "No installed Ollama model found on this computer."

        if not OllamaEnhancer.ensure_server_running():
            return False, "Failed to start Ollama service (ollama serve is not responding)."

        # Strip any previous Ollama block from core_plan
        base_plan = system.core_plan
        if OLLAMA_MARKER in base_plan:
            idx = base_plan.find(f"\n\n[{OLLAMA_MARKER}")
            if idx != -1:
                base_plan = base_plan[:idx].strip()

        modules_desc: List[str] = []
        for p in system.planets:
            sat_names = ", ".join(s.title for s in p.satellites[:6])
            modules_desc.append(f"- {p.title}: {p.summary} (Sub-components: {sat_names})")

        workspace_evidence = OllamaEnhancer._collect_workspace_evidence(system)

        prompt = (
            f"You are a senior software architect. Review the architectural summary, modules, "
            f"and file evidence for the project '{system.project_name or system.name}' below.\n\n"
            f"CURRENT SUMMARY:\n{base_plan[:1200]}\n\n"
            f"ARCHITECTURAL MODULES:\n" + "\n".join(modules_desc[:8]) + "\n\n"
        )
        if workspace_evidence:
            prompt += f"DIRECTORY & FILE EVIDENCE:\n{workspace_evidence[:1000]}\n\n"

        prompt += (
            "YOUR TASK:\n"
            "Summarize WHAT THIS PROJECT DOES, ITS DOMAIN, and HOW IT WORKS in clear, concise English "
            "using at most 3 short bullet points (no more than 80-100 words total).\n"
            "- Do not write an introductory sentence; start directly with '• ' bullet points."
        )

        payload = json.dumps(
            {
                "model": chosen_model,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": 0.2,
                    "num_ctx": 1536,
                    "num_predict": 165,
                },
            }
        ).encode("utf-8")

        try:
            req = urllib.request.Request(
                f"{OLLAMA_BASE_URL}/api/generate",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with _DIRECT_OPENER.open(req, timeout=90.0) as resp:
                result = json.loads(resp.read().decode("utf-8", errors="replace"))
                ai_text = str(result.get("response", "")).strip()
        except urllib.error.HTTPError as http_err:
            err_body = ""
            try:
                err_json = json.loads(http_err.read().decode("utf-8", errors="replace"))
                err_body = str(err_json.get("error", ""))
            except Exception:
                pass
            detail = f" ({err_body})" if err_body else ""
            return False, f"Ollama model error: {http_err}{detail}"
        except Exception as exc:
            return False, f"Ollama response error: {exc}"

        if not ai_text:
            return False, "Ollama returned an empty response."

        # Strip markdown bolding and conversational prefixes, and finish on a complete sentence
        clean_ai = ai_text.replace("**", "").strip()
        lines = [
            ln.strip()
            for ln in clean_ai.splitlines()
            if ln.strip()
            and not ln.strip().lower().startswith(("here is", "here's", "sure", "certainly"))
        ]
        clean_ai = "\n".join(lines).strip()
        if clean_ai and clean_ai[-1] not in ".!?)":
            last_dot = max(clean_ai.rfind("."), clean_ai.rfind("!"))
            if last_dot > len(clean_ai) // 2:
                clean_ai = clean_ai[: last_dot + 1].strip()
        system.core_plan = (
            f"{base_plan}\n\n"
            f"[{OLLAMA_MARKER} — {chosen_model}]\n"
            f"{clean_ai}"
        )
        system.ollama_enhanced = True
        return True, f"'{system.project_name}' summary verified and enhanced with Ollama ({chosen_model})."


class OllamaEnhanceWorker(QThread):
    """Background worker thread that runs Ollama analysis without freezing the UI."""

    progressText = Signal(str)
    finishedEnhance = Signal(bool, str)

    def __init__(
        self,
        systems: List[SolarSystem],
        model_name: Optional[str] = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.systems = systems
        self.model_name = model_name

    def run(self) -> None:
        total = len(self.systems)
        if total == 0:
            self.finishedEnhance.emit(False, "No projects found to analyze.")
            return

        model = self.model_name or OllamaEnhancer.get_preferred_model() or "gemma3:4b"
        success_count = 0
        last_msg = ""

        for idx, sys_obj in enumerate(self.systems, start=1):
            proj_title = sys_obj.project_name or sys_obj.name
            self.progressText.emit(
                f"Ollama ({model}) analyzing ({idx}/{total}): {proj_title}..."
            )
            ok, msg = OllamaEnhancer.enhance_system(sys_obj, model_name=model)
            last_msg = msg
            if ok:
                success_count += 1

        if success_count > 0:
            self.finishedEnhance.emit(
                True,
                f"Enhanced core architecture summaries for {success_count}/{total} projects with Ollama ({model}).",
            )
        else:
            self.finishedEnhance.emit(False, last_msg or "Ollama analysis could not be completed.")


class OllamaPullWorker(QThread):
    """Background worker thread that downloads a new model via the Ollama API."""

    progressText = Signal(str)
    finishedPull = Signal(bool, str)

    def __init__(self, model_name: str, parent: Optional[object] = None) -> None:
        super().__init__(parent)
        self.model_name = model_name.strip()

    def run(self) -> None:
        if not self.model_name:
            self.finishedPull.emit(False, "Please enter a valid model name.")
            return

        if not OllamaEnhancer.ensure_server_running():
            self.finishedPull.emit(False, "Failed to start the Ollama service.")
            return

        self.progressText.emit(f"Starting download for '{self.model_name}'...")
        payload = json.dumps({"name": self.model_name, "stream": True}).encode("utf-8")
        req = urllib.request.Request(
            f"{OLLAMA_BASE_URL}/api/pull",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with _DIRECT_OPENER.open(req, timeout=900.0) as resp:
                for line in resp:
                    if not line:
                        continue
                    try:
                        chunk = json.loads(line.decode("utf-8", errors="replace"))
                        status = chunk.get("status", "")
                        completed = chunk.get("completed", 0)
                        total = chunk.get("total", 0)
                        if total > 0 and completed > 0:
                            pct = round((completed / total) * 100, 1)
                            self.progressText.emit(f"{status}: {pct}%")
                        elif status:
                            self.progressText.emit(status)
                    except Exception:
                        pass
            self.finishedPull.emit(True, f"'{self.model_name}' downloaded and registered!")
        except Exception as exc:
            self.finishedPull.emit(False, f"Download error: {exc}")
