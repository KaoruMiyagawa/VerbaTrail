"""Generate the pinned original Listening assessment audio with local Piper.

The model is deliberately external to the repository. See the provenance note
for the exact voice revision, SHA-256, and license chain.
"""

from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
from pathlib import Path

from app.content import listening_initial_assessment as content

EXPECTED_MODEL_SHA256 = "ABF4B998E34045CBD00EAF050DE2DE620F81DED6EB8B85A24B022A1C2B3F08D2"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, required=True, help="External en_US-joe-medium ONNX model")
    parser.add_argument("--config", type=Path, required=True, help="Matching Piper model JSON config")
    parser.add_argument("--output", type=Path, default=content.AUDIO_PATH)
    args = parser.parse_args()
    if sha256(args.model) != EXPECTED_MODEL_SHA256:
        parser.error("Model SHA-256 does not match the pinned en_US-joe-medium revision.")
    if content.SCRIPT != content.SCRIPT_PATH.read_text(encoding="utf-8").strip():
        parser.error("Authored script changed after import; restart generation with reviewed content.")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable, "-m", "piper", "--model", str(args.model), "--config", str(args.config),
        "--input-file", str(content.SCRIPT_PATH), "--output-file", str(args.output),
        "--sentence-silence", "0.24",
    ]
    subprocess.run(command, check=True)
    print(f"audio={args.output}")
    print(f"sha256={sha256(args.output)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
