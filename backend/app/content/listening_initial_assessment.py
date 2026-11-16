"""Version-pinned original Listening initial-assessment content."""

from pathlib import Path
from typing import Any

CONTENT_VERSION = "listening-initial-community-arts-v1"
RULE_VERSION = "listening-mcq-exact-choice-v1"
TASK_ID = "listening-community-arts-workshop-v1"
TOPIC_ID = "assessment-listening-community-arts-v1"
AUDIO_FILENAME = "initial-community-arts-v1.wav"
AUDIO_URL = f"/audio/listening/{AUDIO_FILENAME}"
AUDIO_SHA256 = "739E3D1263195E63386E04103B21BD34A1701E86549EA81CE35768482C974CC7"
PROVENANCE_SUMMARY = (
    "Original VerbaTrail script, locally synthesized with Piper 1.8.0 and en_US-joe-medium "
    "from pinned rhasspy/piper-voices revision 2f358b6a560e3a913ebf509b2f3344f20b367f94; CC0 voice chain."
)
AUDIO_PATH = Path(__file__).resolve().parents[3] / "frontend" / "public" / "audio" / "listening" / AUDIO_FILENAME
SCRIPT_PATH = Path(__file__).with_name("listening_initial_assessment_script.txt")

SCRIPT = SCRIPT_PATH.read_text(encoding="utf-8").strip()

# Correct positions are deliberately mixed (2, 4, 1, 3, 2, 4).
# The items cover explicit details, cause/effect, inference, and speaker purpose.
QUESTIONS: list[dict[str, Any]] = [
    {
        "id": f"{CONTENT_VERSION}-q1",
        "prompt": "On which day will the printmaking workshop take place?",
        "options": ["Friday", "Saturday", "Sunday", "Monday"],
        "answer": "Saturday",
        "explanation": "Rowan confirms that the workshop is still on Saturday.",
        "competency_id": "listening_detail",
        "item_type": "explicit_detail",
    },
    {
        "id": f"{CONTENT_VERSION}-q2",
        "prompt": "What time will the workshop begin?",
        "options": ["10:00", "10:15", "10:45", "10:30"],
        "answer": "10:30",
        "explanation": "The revised start time is spoken as ten thirty, half an hour later than the leaflet time.",
        "competency_id": "listening_detail",
        "item_type": "explicit_detail",
    },
    {
        "id": f"{CONTENT_VERSION}-q3",
        "prompt": "Which entrance should the participant use?",
        "options": ["The side entrance beside the bicycle shelter", "The main doors", "The café entrance", "The rear delivery entrance"],
        "answer": "The side entrance beside the bicycle shelter",
        "explanation": "Rowan directs participants to the side entrance beside the bicycle shelter and says the main doors will remain locked.",
        "competency_id": "listening_detail",
        "item_type": "explicit_detail",
    },
    {
        "id": f"{CONTENT_VERSION}-q4",
        "prompt": "Why has the class moved to a different room?",
        "options": ["The café needs the studio", "The class has more participants", "Electricians need the main hall", "The bicycle shelter is being repaired"],
        "answer": "Electricians need the main hall",
        "explanation": "Rowan says the class moved because electricians need the main hall that morning.",
        "competency_id": "listening_cause_effect",
        "item_type": "cause_effect",
    },
    {
        "id": f"{CONTENT_VERSION}-q5",
        "prompt": "What should a participant do if a delayed bus makes them arrive after 10:40?",
        "options": ["Wait until the café opens", "Ring the bell marked “workshops”", "Go around to the main doors", "Call the electrician"],
        "answer": "Ring the bell marked “workshops”",
        "explanation": "For arrivals after ten forty, Rowan says to ring the workshops bell so reception can let the participant in.",
        "competency_id": "listening_inference",
        "item_type": "action_inference",
    },
    {
        "id": f"{CONTENT_VERSION}-q6",
        "prompt": "Why did Rowan leave this message?",
        "options": ["To advertise a different workshop", "To ask the participant to bring paper and ink", "To cancel the Saturday session", "To confirm revised arrangements and ask for a reply only if attendance is no longer possible"],
        "answer": "To confirm revised arrangements and ask for a reply only if attendance is no longer possible",
        "explanation": "Rowan says the centre needs to confirm the updated arrangements were received and asks for a reply only if the participant cannot attend.",
        "competency_id": "listening_speaker_purpose",
        "item_type": "speaker_purpose",
    },
]

LIMITATIONS = [
    "六道原创客观题只观察本材料中的细节、因果、行动推断和说话者目的理解；题量不足以估计整体听力能力或 IELTS Band。",
    "重复曝光只依据 VerbaTrail 中记录的相同版本练习会话和题目；无法识别平台外听过的材料。",
]

DESIGN_RATIONALE = (
    "一段约一分钟的原创单人通知，包含修正后的日期/时间、入口指示、房间变更原因、迟到应对和留言目的。"
    "六题覆盖信息定位、因果、行动推断与说话者目的；正确项位置为 2、4、1、3、2、4，"
    "不依赖固定选项位置。单段短测评标记 evidence_insufficient，不推断整体听力水平或 Band。"
)
