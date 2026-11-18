"""Original, narrowly scoped writing prompt for initial assessment collection."""

TASK_ID = "writing-initial-community-land-use"
TASK_VERSION = "writing-initial-community-land-use-v1"
PROMPT_ID = "community-land-use-choice"
PROMPT_VERSION = "community-land-use-choice-v1"
SUBMISSION_SOURCE = "assessment_writing_submission"
MAX_CHARACTERS = 20000

PROMPT = {
    "task_id": TASK_ID,
    "task_version": TASK_VERSION,
    "prompt_id": PROMPT_ID,
    "prompt_version": PROMPT_VERSION,
    "text": (
        "Some towns have unused land that could become either a community garden "
        "or an outdoor sports area. Which use would benefit local residents more? "
        "Give reasons for your choice and consider the alternative."
    ),
    "instructions": "Write one response in English. Support your view with reasons and examples. You may save a draft before submitting your final response.",
    "suggested_words": 250,
    "max_characters": MAX_CHARACTERS,
    "source": "Original prompt written for VerbaTrail; no IELTS test material used.",
    "scope": "One original Task 2-style response collection task; it does not cover the full IELTS Writing test.",
}

LIMITATIONS = [
    "This task collects one text response and is not a calibrated writing proficiency measurement.",
    "Word count, paragraph count, completion, and editing activity do not establish writing ability or an IELTS Writing Band.",
    "Task Response, Coherence and Cohesion, Lexical Resource, and Grammatical Range and Accuracy are not assessed.",
]
