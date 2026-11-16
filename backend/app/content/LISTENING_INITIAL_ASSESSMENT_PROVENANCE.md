# Listening Initial Assessment Audio Provenance

## Authored material

- Content ID: `listening-initial-community-arts-v1`
- Scenario: an arts-centre staff member leaves a voicemail about revised arrangements for a printmaking workshop.
- Original English source text: [`listening_initial_assessment_script.txt`](listening_initial_assessment_script.txt)
- The script and six objective questions were authored for VerbaTrail. No IELTS question, branded test content, third-party script, or external recording was used.
- Competencies sampled: explicit detail, cause/effect, action inference, and speaker purpose. Correct answer positions are 2, 4, 1, 3, 2, 4.

## Synthetic speech and license chain

- The audio is genuine generated speech, not silence or a placeholder. It was generated locally/offline from the authored script with Piper TTS 1.8.0.
- Voice: `en_US-joe-medium`, repository revision [`2f358b6a560e3a913ebf509b2f3344f20b367f94`](https://huggingface.co/rhasspy/piper-voices/tree/2f358b6a560e3a913ebf509b2f3344f20b367f94/en/en_US/joe/medium).
- The pinned [voice MODEL_CARD](https://huggingface.co/rhasspy/piper-voices/blob/2f358b6a560e3a913ebf509b2f3344f20b367f94/en/en_US/joe/medium/MODEL_CARD) identifies the voice as CC0 and its `rdh` base as CC0. Piper's [voice-chain licensing guidance](https://github.com/OHF-Voice/piper1-gpl/blob/main/docs/COMMERCIAL_USE.md) lists this exact voice in its CC0 chain. The Piper engine itself is GPL-3.0-or-later and is not distributed with this project.
- Model file SHA-256: `ABF4B998E34045CBD00EAF050DE2DE620F81DED6EB8B85A24B022A1C2B3F08D2`. The ONNX model is not included in the repository.
- Reproduction: run `backend/scripts/generate_listening_initial_assessment_audio.py` with the model and matching config downloaded from the pinned voice revision. The script refuses a model whose hash does not match.
- Audio file: [`initial-community-arts-v1.wav`](../../../frontend/public/audio/listening/initial-community-arts-v1.wav).
- Output: mono PCM WAV, 22,050 Hz, 16-bit, 58.8053 seconds; SHA-256 `739E3D1263195E63386E04103B21BD34A1701E86549EA81CE35768482C974CC7` (2,593,356 bytes).

This is synthetic speech from a licensed voice chain, not a human speaker recording. Model licensing notes describe training-data provenance and are not legal advice.
