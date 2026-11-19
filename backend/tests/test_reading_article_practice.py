"""Regression coverage for article discovery, private keys and shared attempt history."""

from concurrent.futures import ThreadPoolExecutor

from app.content.reading_practice import reading_articles, reading_practice_pack
from app.persistence.seed import import_content_pack

BASE = "/api/v1/reading/articles"
PROFILE = "/api/v1/profile/exam-preparation"


def choose_exam(client, learner, variant):
    response = client.put(PROFILE, params={"learner_id": learner}, json={"exam_variant": variant})
    assert response.status_code == 200, response.text


def publish(session_factory):
    with session_factory.begin() as db:
        import_content_pack(db, pack_data=reading_practice_pack())


def test_catalog_distinguishes_selection_publication_and_exam_type(client, session_factory):
    assert client.get(BASE).json()["status"] == "selection_required"
    choose_exam(client, "default_learner", "general_training")
    assert client.get(BASE).json()["status"] == "empty"
    choose_exam(client, "default_learner", "academic")
    assert client.get(BASE).json()["status"] == "unimported"
    publish(session_factory)
    catalog = client.get(BASE).json()
    assert catalog["status"] == "ready" and catalog["missing_count"] == 0
    assert len(catalog["articles"]) == 3
    for entry in catalog["articles"]:
        detail = client.get(f"{BASE}/{entry['id']}").json()
        article = detail["article"]
        assert detail["result"] is None and entry["status"] == "not_started"
        assert len(article["paragraphs"]) == 6 and 600 <= article["word_count"] <= 850
        items = [item for section in article["sections"] for item in section["items"]]
        assert len(items) == entry["question_count"] == 12
        assert all(set(item) == {"id", "number", "prompt", "options"} for item in items)
        answers = {item.id: item.answer for item in reading_articles()[entry["id"]].items}
        submitted = client.post(f"{BASE}/{entry['id']}/submit", json={"answers": answers})
        assert submitted.status_code == 200, submitted.text
        assert submitted.json()["result"]["correct_count"] == 12


def test_heading_group_and_choice_submission_recover_in_shared_history(client, session_factory):
    publish(session_factory)
    choose_exam(client, "default_learner", "academic")
    choose_exam(client, "other-learner", "academic")
    source = reading_articles()["ocean-exploration-v1"]
    url = f"{BASE}/{source.id}"
    submit = f"{url}/submit"
    correct = {item.id: item.answer for item in source.items}
    first, second = source.sections[0].items[:2]
    duplicate_heading = correct | {second.id: first.answer}
    assert client.post(submit, json={"answers": duplicate_heading}).status_code == 422
    assert client.get(url).json()["result"] is None

    last = source.items[-1]
    wrong_option = next(option for option in last.options if option != last.answer)
    payload = {"answers": correct | {last.id: wrong_option}}
    with ThreadPoolExecutor(max_workers=2) as pool:
        first_submit = pool.submit(client.post, submit, json=payload)
        retry = pool.submit(client.post, submit, json=payload)
        response, repeated = first_submit.result(), retry.result()
    assert response.status_code == repeated.status_code == 200, (response.text, repeated.text)
    result = response.json()["result"]
    assert repeated.json()["result"] == result
    assert result["correct_count"] == 11 and result["total_count"] == 12
    paragraphs = {p["id"]: p["text"] for p in response.json()["article"]["paragraphs"]}
    assert all(item["evidence"]["quote"] in paragraphs[item["evidence"]["paragraph"]] for item in result["items"])
    assert result["items"][-1]["correct_option"] == last.answer
    assert client.post(submit, json={"answers": correct}).status_code == 409
    assert client.post(submit, json=payload).json()["result"] == result
    assert client.get(url, params={"learner_id": "other-learner"}).json()["result"] is None

    publish(session_factory)
    assert client.get(url).json()["result"] == result
    history = client.get(f"/api/v1/sessions/{result['session_id']}/attempts")
    assert history.status_code == 200, history.text
    attempts = history.json()
    assert len(attempts) == 8
    grouped = [item["group_evaluation"] for item in attempts if item["group_evaluation"] is not None]
    assert len(grouped) == 1 and grouped[0]["correct_count"] == 5
