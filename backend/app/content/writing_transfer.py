"""Original Writing transfer materials. Changes require a new content version."""

from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.decision.writing_agent_schemas import WritingAspect

CATALOG_VERSION = "writing-independent-transfer-v1"
MAX_CHARACTERS = 4000


class WritingTransferTask(BaseModel):
    model_config = ConfigDict(frozen=True)

    task_id: str
    content_version: str
    material_id: str
    topic_id: str
    aspect: WritingAspect
    title: str
    material: str
    prompt: str
    source: Literal["Original VerbaTrail Writing transfer material"] = "Original VerbaTrail Writing transfer material"
    catalog_version: str = CATALOG_VERSION
    max_characters: int = MAX_CHARACTERS


CATALOG = (
    WritingTransferTask(
        task_id="writing-transfer-school-archive", content_version="school-archive-v1",
        material_id="school-archive", topic_id="historical-records", aspect="position",
        title="A school's archive",
        material="A school can digitise either handwritten alumni letters or photographs of school events this year. It plans to share the chosen collection with current students.",
        prompt="Write a brief recommendation to the head teacher. Make your position clear and explain how your choice serves current students.",
    ),
    WritingTransferTask(
        task_id="writing-transfer-exhibition-labels", content_version="exhibition-labels-v1",
        material_id="exhibition-labels", topic_id="museum-interpretation", aspect="position",
        title="Labels for a new exhibition",
        material="A museum is redesigning its labels for first-time visitors. It can put one short label beside each object, or give visitors a single guide linking the objects to one another.",
        prompt="Recommend an approach to the curator. State the approach you favour and explain how it addresses the needs of first-time visitors.",
    ),
    WritingTransferTask(
        task_id="writing-transfer-weather-notices", content_version="weather-notices-v1",
        material_id="weather-notices", topic_id="public-weather-communication", aspect="reasons",
        title="How to issue ferry updates",
        material="A ferry operator is deciding whether to add live announcements at the terminal or send updates by text on days when weather disrupts the timetable.",
        prompt="Recommend one option to the operator. Develop one reason for your recommendation, explaining how it would help in a specific disruption.",
    ),
    WritingTransferTask(
        task_id="writing-transfer-tool-instructions", content_version="tool-instructions-v1",
        material_id="tool-instructions", topic_id="workshop-induction", aspect="reasons",
        title="Instructions for first-time workshop users",
        material="A repair workshop can provide either printed instructions beside each tool or a short demonstration at the beginning of every session.",
        prompt="Explain which approach you would try first. Develop one reason and use a concrete example to show how it would affect a new user.",
    ),
    WritingTransferTask(
        task_id="writing-transfer-meeting-record", content_version="meeting-record-v1",
        material_id="meeting-record", topic_id="team-communication", aspect="reasoning_link",
        title="A record of team decisions",
        material="A trial team posts full meeting notes on Friday and a short decision list on the day of each meeting. Staff in another time zone say they often need to act before Friday.",
        prompt="Write a recommendation on which record the team should prioritise. Use a detail from the trial and explain the reasoning that connects it to your recommendation.",
    ),
    WritingTransferTask(
        task_id="writing-transfer-library-pickup", content_version="library-pickup-v1",
        material_id="library-pickup", topic_id="library-access", aspect="reasoning_link",
        title="Collecting reserved library items",
        material="A library can extend its staffed pickup window or install lockers for reserved items. Its pilot found that many pickups happened after closing time, while some visitors needed staff help finding the right item.",
        prompt="Recommend one approach. Refer to a detail from the pilot and show how it supports your recommendation, taking the other detail into account.",
    ),
)
