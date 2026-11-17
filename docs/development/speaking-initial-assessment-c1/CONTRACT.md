# Speaking initial assessment C1

An existing `AssessmentRun` has one Speaking task. The original VerbaTrail prompt asks the learner to explain how they would teach a skill at a neighborhood exchange. The task ID is `speaking-initial-community-skill`, task version is `speaking-initial-community-skill-v1`, prompt ID is `community-skill-exchange`, and prompt version is `community-skill-exchange-v1`. The suggested response length is 60 seconds. Duration is never used to evaluate ability.

| HTTP | Resource under `/api/v1/assessment-runs/{run_id}/speaking` | Input and effect |
| --- | --- | --- |
| GET | Base path | `learner_id` query. Returns the current task and any pinned prompt and registration. |
| POST | `/start` | `learner_id` query. Pins the version and returns the prompt. Repeats return the stored task state. |
| POST | `/complete` | JSON with `learner_id`, `recording_id`, `task_version`, `prompt_id`, `prompt_version`, `duration_seconds`, `recorded_at`, and `idempotency_key`. |

An exact replay of the completion, including its original idempotency key, returns the existing registration. A different completion or a recording ID already bound to another assessment returns 409. The server scopes reads and writes to the supplied learner and returns 404 for another learner's run. Learner ID is the existing development scope and provides no authentication. The timestamp must carry a time zone and is stored in UTC. The metadata bounds accept positive durations up to 600 seconds and serve input validation.

An unstarted `initial-v2` run gets a prompt only when its Speaking task is started. Earlier or unsupported unpinned versions return 409, as do unavailable pinned versions. Starting a second run after an earlier run pinned the same Speaking version marks `repeated_exposure`. This is recorded task exposure. The server cannot know whether the learner read the prompt or spoke. A completed task with missing or inconsistent metadata returns 409 rather than claiming that a recording can be restored.

Migration `0012_speaking_assessment` adds a table keyed by recording ID with a unique foreign key to the assessment task. That row contains a client-reported recording ID, duration and timestamp, plus the server registration time and retry key. It is separate from `SpeakingPractice` history and does not need a `LearningSession`, `Attempt`, or audio column. Completing Speaking updates only that task and the run's update timestamp.

The intended client flow is to store the audio Blob in its local IndexedDB before posting metadata, and to check that store for playback availability. C1 introduces only the server contract. The server rejects extra fields such as audio and transcripts. It reports the registration as `client_reported`, server audio availability as false, local audio availability as `unknown`, and evaluation as `not_assessed`. Learner Model projects a linked completion as `assessment_activity` with validity `not_evaluated`. It grants no positive evidence level for the metadata.

To reproduce, run `python -m scripts.verify_speaking_assessment_http` from `backend/` with the project Python environment. The script migrates a temporary SQLite database from `0011` to `0012`, starts and restarts Uvicorn, and writes [the HTTP report](http-evidence.json). Its synthetic recording metadata verifies the API contract. It does not verify microphone capture or client storage.
