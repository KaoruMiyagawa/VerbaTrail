"""Original VerbaTrail prompt. Changes to the task require a new version."""

TASK_VERSION = "speaking-initial-community-skill-v1"
RECORDING_SOURCE = "speaking_assessment_recording"
PROMPT = {
    "task_id": "speaking-initial-community-skill",
    "prompt_id": "community-skill-exchange",
    "prompt_version": "community-skill-exchange-v1",
    "text": (
        "Your neighborhood is starting a weekly skill exchange. Tell the organizer "
        "about a practical skill you could teach a small group. Explain what the "
        "group would do first, and how you would help someone who found that step difficult."
    ),
    "instructions": "Record one response in English. Save the audio in this browser before registering its metadata.",
    "suggested_seconds": 60,
    "source": "Original prompt written for VerbaTrail; no IELTS test material used.",
}
LIMITATIONS = [
    "Recording metadata is reported by the client. The server cannot verify that a recording exists or evaluate its content or quality.",
    "Audio is intended for local browser storage. Availability on the current device must be checked locally.",
    "Task completion means metadata was registered. Duration is guidance only; speaking ability has not been assessed.",
]
