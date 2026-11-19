"""Private loopback acceptance app and disposable SQLite fixtures for Writing D4."""

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
from hashlib import sha256
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory, gettempdir
import time
from uuid import uuid4

import httpx

BACKEND = Path(__file__).resolve().parents[1]
PREFIX = "verbatrail-writing-d4-"
RUNS = "/api/v1/assessment-runs"
ESSAY = "I support shared gardens 🌱 because neighbours can meet regularly.\n\nA weekly planting session gives people a reason to talk."
ANSWER = "My example explains how regular meetings make cooperation easier."
REVISION = "I support shared gardens because regular meetings help neighbours cooperate."
TRANSFER = "I favour the later schedule because it makes attendance possible for more people."


def digest(body):
    return sha256(body.encode("utf-8")).hexdigest()


def scratch():
    return TemporaryDirectory(prefix=PREFIX)


def database_url(root):
    root = Path(root).resolve(strict=True)
    db = root / "writing-d4.sqlite"
    if root.parent != Path(gettempdir()).resolve() or not root.name.startswith(PREFIX) or db.is_symlink():
        raise ValueError("Writing D4 acceptance requires its own temporary SQLite directory.")
    return f"sqlite:///{db.as_posix()}"


def prepare_database(root, revision="head"):
    from app.persistence.database import create_db_engine
    from app.persistence.migrations_runner import upgrade_database

    url = database_url(root)
    engine = create_db_engine(database_url=url)
    try:
        upgrade_database(database_url=url, engine=engine, revision=revision)
    finally:
        engine.dispose()


def local_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_until(action, *, seconds=15, label):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        result = action()
        if result:
            return result
        time.sleep(0.05)
    raise AssertionError(f"Timed out waiting for {label}")


@contextmanager
def local_process(command, *, cwd, env, logfile):
    with logfile.open("wb") as output:
        process = subprocess.Popen(command, cwd=cwd, env=env, stdout=output, stderr=subprocess.STDOUT)
        try:
            yield process
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


def wait_http(client, path, process, logfile, *, expected=None):
    def ready():
        if process.poll() is not None:
            raise AssertionError(logfile.read_text(encoding="utf-8", errors="replace"))
        try:
            response = client.get(path, timeout=0.5)
            return response.is_success and (expected is None or response.text == expected)
        except httpx.TransportError:
            return False

    wait_until(ready, seconds=30, label=f"{client.base_url}{path} (log: {logfile})")


@contextmanager
def server(root, *, mode="disabled", budget=4096, cors_origin=""):
    token = uuid4().hex
    port = local_port()
    logfile = root / f"uvicorn-{token}.log"
    env = os.environ | {
        "APP_ENV": "testing", "DATABASE_URL": database_url(root),
        "ALLOW_REMOTE_MODEL_CALLS": "false", "MODEL_API_KEY": "",
        "WRITING_D4_ROOT": str(root), "WRITING_D4_TOKEN": token,
        "WRITING_D4_MODE": mode, "WRITING_D4_BUDGET": str(budget), "CORS_ORIGINS": cors_origin,
    }
    command = [sys.executable, "-m", "uvicorn", "scripts.writing_d4_harness:create_test_app",
               "--factory", "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"]
    with local_process(command, cwd=BACKEND, env=env, logfile=logfile) as process:
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=40, trust_env=False) as client:
            wait_http(client, "/__writing_d4/ready", process, logfile, expected=token)
            yield client, process


def _proposal(request):
    data = json.loads(request.user_prompt)
    evidence, quote = next(iter(data["essay"].items()))
    prior = data.get("prior_hypotheses")
    hypotheses = [
        {"id": identifier, "statement": statement, "focus_aspect": aspect,
         "status": "candidate", "unresolved_question": "What connects the example to the position?", "evidence_links": []}
        for identifier, statement, aspect in (
            ("position", "The position may need to be more specific.", "position"),
            ("connection", "The example may need a link to the position.", "reasoning_link"),
        )
    ]
    if prior:
        for hypothesis, previous in zip(hypotheses, prior, strict=True):
            hypothesis.update(id=previous["id"], statement=previous["statement"], status="insufficient_evidence")
    return {
        "argument_analysis": [
            {"aspect": aspect, "interpretation": "The essay gives this passage as context.",
             "evidence_ids": [evidence], "excerpt": quote[:40], "assessment": "candidate"}
            for aspect in ("position", "reasons", "reasoning_link")
        ],
        "hypotheses": hypotheses,
        "followup": {
            "question": "What is the link between your example and your position?",
            "hypothesis_ids": ["position", "connection"], "evidence_ids": [evidence],
            "discriminating_reason": "The answer may distinguish the explanations.",
        } if data["can_ask"] else None,
        "decision_reason": "Investigate two possible explanations.",
        "unresolved_uncertainties": ["Context alone does not establish the quality of the argument."],
    }


def create_test_app():
    from fastapi.responses import PlainTextResponse

    from app.api.v1.dependencies import get_model_provider
    from app.application import writing_agent_service
    from app.config import Settings, get_settings
    from app.main import create_app
    from app.model_provider.errors import ModelBudgetExceededError, ModelTimeoutError
    from app.model_provider.fake import FakeModelProvider
    from app.model_provider.schemas import TokenUsage

    root = Path(os.environ["WRITING_D4_ROOT"])
    mode = os.environ["WRITING_D4_MODE"]
    if mode not in {"disabled", "valid", "hold", "timeout", "provider-budget", "overrun", "json",
                    "evidence", "excerpt", "answer-only"}:
        raise ValueError(f"Unknown Writing D4 provider scenario: {mode}")
    settings = Settings(
        _env_file=None, APP_ENV="production", API_V1_PREFIX="/api/v1",
        DATABASE_URL=database_url(root), CORS_ORIGINS=os.environ["CORS_ORIGINS"],
        ALLOW_REMOTE_MODEL_CALLS=mode != "disabled", MODEL_API_KEY="", MODEL_TIMEOUT_SECONDS=1,
        MODEL_MAX_TOKENS_PER_CALL=1024,
        MAX_DIAGNOSTIC_FOLLOWUP_TURNS=1, DIAGNOSTIC_SESSION_TOKEN_BUDGET=int(os.environ["WRITING_D4_BUDGET"]),
    )
    app = create_app(settings)
    now = writing_agent_service.utc_now
    writing_agent_service.utc_now = lambda: now() + timedelta(minutes=10 * (root / "expire").exists())

    def scripted(request):
        with (root / "calls.log").open("a", encoding="utf-8") as ledger:
            ledger.write(f"{request.max_attempts}\n")
        if mode == "hold":
            wait_until((root / "release").exists, seconds=30, label="test provider release")
        if mode == "timeout":
            return ModelTimeoutError("Synthetic timeout")
        if mode == "provider-budget":
            return ModelBudgetExceededError("Synthetic provider budget limit")
        if mode == "json":
            return "{invalid JSON"
        proposal = _proposal(request)
        if mode == "evidence":
            proposal["argument_analysis"][0]["evidence_ids"] = [(root / "foreign-evidence").read_text(encoding="utf-8")]
        if mode == "excerpt":
            proposal["argument_analysis"][0]["excerpt"] = "Invented quotation"
        if mode == "answer-only" and "learner_answer" in json.loads(request.user_prompt):
            answer = json.loads(request.user_prompt)["learner_answer"]
            proposal["hypotheses"][0].update(status="supported", evidence_links=[{
                "evidence_id": answer["evidence_id"], "excerpt": answer["body"],
                "relation": "supports", "reason": "This only cites the follow-up answer.",
            }])
        prompt_tokens = 5000 if mode == "overrun" else 9
        return proposal, TokenUsage(prompt_tokens=prompt_tokens, completion_tokens=3, total_tokens=prompt_tokens + 3)

    provider = FakeModelProvider(
        [scripted, scripted], provider_name="writing-d4-test-double", max_retries=0,
    )
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_model_provider] = lambda: provider

    @app.get("/__writing_d4/ready", response_class=PlainTextResponse)
    def ready():
        return os.environ["WRITING_D4_TOKEN"]

    return app


@dataclass(frozen=True)
class WritingRun:
    client: httpx.Client
    run_id: str
    learner: str

    @classmethod
    def create(cls, client, learner="alice"):
        response = client.post(RUNS, json={"learner_id": learner, "assessment_version": "initial-v2",
                                          "idempotency_key": uuid4().hex})
        assert response.status_code == 200, response.text
        return cls(client, response.json()["run_id"], learner)

    def request(self, method, path="", payload=None, *, learner=None):
        return self.client.request(method, f"{RUNS}/{self.run_id}/writing{path}",
                                   params={"learner_id": learner or self.learner}, json=payload)

    def call(self, method, path="", payload=None, *, status=200, learner=None):
        response = self.request(method, path, payload, learner=learner)
        assert response.status_code == status, f"{method} {path}: {response.status_code} {response.text}"
        return response.json()

    def submit_essay(self, body=ESSAY):
        task = self.call("POST", "/start")
        payload = {field: task["prompt"][field] for field in ("task_version", "prompt_id", "prompt_version")}
        payload.update(learner_id=self.learner, body=body, version=task["draft"]["version"],
                       idempotency_key=uuid4().hex)
        return payload, self.call("POST", "/submit", payload)["submission"]

    def finish_training(self, body=ESSAY):
        _, submission = self.submit_essay(body)
        initial = self.call("POST", "/diagnostic/start", {"idempotency_key": uuid4().hex})["initial"]
        answer = self.call("POST", "/diagnostic/answer", {
            "question_id": initial["id"], "body": ANSWER, "idempotency_key": uuid4().hex,
        })["followup"]
        practice = self.call("POST", "/training/start")
        completed = self.call("POST", "/training/submit", {
            "body": REVISION, "version": practice["draft"]["version"], "idempotency_key": uuid4().hex,
        })
        return submission, initial, answer, completed
