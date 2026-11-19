"""Drive the Writing D4 UI in Chromium using a disposable Uvicorn and Vite pair."""

import os
from pathlib import Path
import re
from urllib.parse import urlsplit

import httpx

from scripts.writing_d4_harness import (
    ANSWER, BACKEND, ESSAY, REVISION, RUNS, TRANSFER, WritingRun,
    local_port, local_process, prepare_database, scratch, server, wait_http,
)

FRONTEND = BACKEND.parent / "frontend"
LEARNER = "default_learner"
SELECTED = f"assessment:selected:{LEARNER}"
SKILL = f"assessment:skill:{LEARNER}"
WRITING_TAB = '[data-assessment-skill="writing"]'
ESSAY_EDITOR = "#assessment-writing-body"
REVISION_EDITOR = "#writing-practice-revision"
TRANSFER_EDITOR = "#writing-transfer-body"


def button(page, label):
    return page.get_by_role("button", name=label, exact=True)


def heading(page, label):
    return page.get_by_role("heading", name=label, exact=True)


def create_runs(client, count):
    return [WritingRun.create(client, LEARNER) for _ in range(count)]


def init_context(browser, run, ui, *, block_storage=False):
    context = browser.new_context(service_workers="block")
    context.set_default_timeout(10_000)
    ui_host = urlsplit(ui).netloc
    api_host = urlsplit(str(run.client.base_url)).netloc

    def local_only(route):
        target = urlsplit(route.request.url)
        # Also prevent the dev server's default /api proxy from receiving a test request.
        allowed = (target.netloc == api_host or
                   (target.netloc == ui_host and target.path != "/api" and not target.path.startswith("/api/")))
        if target.scheme in {"http", "https"} and not allowed:
            route.abort("blockedbyclient")
        else:
            route.continue_()

    context.route("**/*", local_only)
    context.add_init_script(f"""
        if (location.hostname === '127.0.0.1') {{
            if (!localStorage.getItem({SELECTED!r})) localStorage.setItem({SELECTED!r}, {run.run_id!r});
            localStorage.setItem({SKILL!r}, 'writing');
        }}
    """)
    if block_storage:
        context.add_init_script("""
            const set = Storage.prototype.setItem;
            Storage.prototype.setItem = function (key, value) {
                if (this === sessionStorage && key.startsWith('assessment:writing-')) {
                    throw new DOMException('Simulated storage failure', 'QuotaExceededError');
                }
                return set.call(this, key, value);
            };
        """)
    return context


def enroll(context, ui):
    page = context.new_page()
    page.on("dialog", lambda dialog: dialog.accept())
    page.goto(f"{ui}/learn/assessment", wait_until="domcontentloaded")
    return page


def switch_run(page, run, *, expect):
    response = run.client.get(RUNS, params={"learner_id": run.learner})
    response.raise_for_status()
    ordered = [item["run_id"] for item in response.json()]
    history = page.locator("section").filter(has=heading(page, "评估记录"))
    records = history.locator("ul button")
    expect(records).to_have_count(len(ordered))
    target = records.nth(ordered.index(run.run_id))
    expect(target).to_have_attribute("aria-pressed", "false")
    target.click()
    page.wait_for_function("(keyAndId) => localStorage.getItem(keyAndId[0]) === keyAndId[1]",
                           arg=[SELECTED, run.run_id])


def start_essay(page):
    button(page, "开始 Writing").click()
    return page.locator(ESSAY_EDITOR)


def verify_drafts_and_tabs(browser, client, ui, expect):
    run, other = create_runs(client, 2)
    context = init_context(browser, run, ui)
    try:
        page = enroll(context, ui)
        editor = start_essay(page)
        editor.fill(ESSAY)
        button(page, "保存服务端草稿").click()
        expect(page.locator("#assessment-writing-save-state")).to_contain_text("服务端草稿已保存")
        assert run.call("GET")["draft"]["body"] == ESSAY
        page.reload(wait_until="domcontentloaded")
        expect(editor).to_have_value(ESSAY)

        original_local = ESSAY + " This edit stays with the first assessment."
        editor.fill(original_local)
        switch_run(page, other, expect=expect)
        second = start_essay(page)
        expect(second).to_have_value("")
        second.fill("An unrelated draft in another assessment.")
        switch_run(page, run, expect=expect)
        expect(editor).to_have_value(original_local)
        assert run.call("GET")["draft"]["body"] == ESSAY

        tab = enroll(context, ui)
        tab_editor = tab.locator(ESSAY_EDITOR)
        expect(tab_editor).to_have_value(ESSAY)
        winner = "The other tab saves this version first."
        tab_editor.fill(winner)
        button(tab, "保存服务端草稿").click()
        expect(tab.locator("#assessment-writing-save-state")).to_contain_text("服务端草稿已保存")
        button(page, "保存服务端草稿").click()
        expect(button(page, "采用服务器草稿")).to_be_visible()
        expect(editor).to_have_value(original_local)
        assert run.call("GET")["draft"]["body"] == winner
        button(page, "采用服务器草稿").click()
        expect(editor).to_have_value(winner)
    finally:
        context.close()


def verify_lost_submit(browser, client, ui, expect, *, commit_before_loss):
    (run,) = create_runs(client, 1)
    context = init_context(browser, run, ui)
    try:
        page = enroll(context, ui)
        editor = start_essay(page)
        editor.fill(ESSAY)
        pattern = re.compile(rf"{re.escape(RUNS)}/{run.run_id}/writing/submit(?:\?.*)?$")
        submitted = []
        stop = True

        def submit_route(route):
            if route.request.method != "POST":
                route.fallback()
                return
            submitted.append(route.request.post_data_json)
            if not stop:
                route.fallback()
                return
            if commit_before_loss:
                assert route.fetch().ok
            route.abort("internetdisconnected")

        page.route(pattern, submit_route)
        button(page, "正式提交作文").click()
        if commit_before_loss:
            expect(heading(page, "作文已提交 · 写作能力未评估")).to_be_visible()
            page.reload(wait_until="domcontentloaded")
            expect(heading(page, "作文已提交 · 写作能力未评估")).to_be_visible()
            assert len(submitted) == 1
        else:
            expect(page.get_by_role("alert").filter(has_text="暂时无法确认提交结果")).to_be_visible()
            assert run.call("GET")["submission"] is None
            assert len(submitted) == 1
            key = f"assessment:writing-recovery:v1:{LEARNER}:{run.run_id}"
            page.reload(wait_until="domcontentloaded")
            expect(editor).to_have_value(ESSAY)
            expect(button(page, "重试正式提交")).to_be_enabled()
            saved = page.evaluate("key => JSON.parse(sessionStorage.getItem(key)).pendingSubmission", key)
            assert saved == submitted[0] and saved["idempotency_key"]
            stop = False
            button(page, "重试正式提交").click()
            expect(heading(page, "作文已提交 · 写作能力未评估")).to_be_visible()
            assert submitted == [saved, saved]
        assert run.call("GET")["submission"]["body"] == ESSAY
    finally:
        context.close()


def verify_training_and_transfer(browser, client, ui, expect):
    (run,) = create_runs(client, 1)
    run.submit_essay()
    context = init_context(browser, run, ui)
    try:
        page = enroll(context, ui)
        button(page, "打开作文诊断").click()
        button(page, "开始诊断").click()
        page.locator("#writing-diagnostic-answer").fill(ANSWER)
        button(page, "保存回答并更新假设").click()
        button(page, "生成探索性练习").click()
        revision = page.locator(REVISION_EDITOR)
        revision.fill(REVISION)
        with page.expect_response(lambda reply: "/writing/training/draft" in reply.url and reply.request.method == "PUT") as saved:
            button(page, "保存修订草稿").click()
        assert saved.value.ok
        page.reload(wait_until="domcontentloaded")
        button(page, "打开作文诊断").click()
        expect(revision).to_have_value(REVISION)
        button(page, "正式提交修订").click()
        expect(heading(page, "原文与正式修订对照")).to_be_visible()
        button(page, "进入独立迁移").click()
        button(page, "开始独立迁移").click()
        transfer = page.locator(TRANSFER_EDITOR)
        expect(transfer).to_have_value("")
        expect(page.locator(WRITING_TAB)).not_to_contain_text(REVISION)
        expect(page.locator(WRITING_TAB)).not_to_contain_text(ANSWER)
        expect(page.locator(WRITING_TAB)).not_to_contain_text(ESSAY.split("\n")[0])
        expect(heading(page, "初始候选假设")).to_have_count(0)
        expect(revision).to_have_count(0)
        transfer.fill(TRANSFER)
        with page.expect_response(lambda reply: "/writing/transfer/draft" in reply.url and reply.request.method == "PUT") as saved:
            button(page, "保存迁移草稿").click()
        assert saved.value.ok
        page.reload(wait_until="domcontentloaded")
        expect(transfer).to_have_value(TRANSFER)
        button(page, "正式提交迁移").click()
        result = heading(page, "迁移作答已保存 · 语义质量未评估")
        expect(result).to_be_visible()
        page.reload(wait_until="domcontentloaded")
        expect(result).to_be_visible()
        expect(page.get_by_role("region", name="迁移作答记录")).to_contain_text(TRANSFER)
        assert run.call("GET", "/transfer")["response"]["observation"]["evaluation_status"] == "not_assessed"
        assert run.call("GET", "/training")["revision"]["body"] == REVISION
        assert run.call("GET")["submission"]["body"] == ESSAY
    finally:
        context.close()


def verify_storage_failure(browser, client, ui, expect):
    run, other = create_runs(client, 2)
    context = init_context(browser, run, ui, block_storage=True)
    try:
        page = enroll(context, ui)
        editor = start_essay(page)
        editor.fill(ESSAY)
        expect(page.get_by_role("alert").filter(has_text="本地恢复存储不可用")).to_be_visible()
        button(page, "正式提交作文").click()
        expect(page.get_by_role("alert").filter(has_text="无法安全保存待确认提交")).to_be_visible()
        assert run.call("GET")["submission"] is None
        expect(editor).to_have_value(ESSAY)
        switch_run(page, other, expect=expect)
        expect(button(page, "开始 Writing")).to_be_visible()
        switch_run(page, run, expect=expect)
        expect(editor).to_have_value(ESSAY)
        button(page, "保存服务端草稿").click()
        expect(page.locator("#assessment-writing-save-state")).to_contain_text("服务端草稿已保存")
        page.reload(wait_until="domcontentloaded")
        expect(editor).to_have_value(ESSAY)
    finally:
        context.close()


def main():
    from playwright.sync_api import expect, sync_playwright

    vite = FRONTEND / "node_modules" / "vite" / "bin" / "vite.js"
    if not vite.is_file():
        raise RuntimeError("The browser check requires the frontend's installed Vite dependencies.")
    port = local_port()
    ui = f"http://127.0.0.1:{port}"
    with scratch() as directory:
        root = Path(directory)
        prepare_database(root)
        with server(root, cors_origin=ui) as (client, _):
            env = os.environ | {"VITE_API_BASE_URL": str(client.base_url).rstrip("/")}
            args = ["node", str(vite), "--host", "127.0.0.1", "--port", str(port), "--strictPort"]
            logfile = root / "vite.log"
            with local_process(args, cwd=FRONTEND, env=env, logfile=logfile) as process:
                with httpx.Client(base_url=ui, trust_env=False) as frontend:
                    wait_http(frontend, "/learn/assessment", process, logfile)
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(executable_path=os.environ.get("CHROME_BIN"))
                    try:
                        verify_drafts_and_tabs(browser, client, ui, expect)
                        verify_lost_submit(browser, client, ui, expect, commit_before_loss=False)
                        verify_lost_submit(browser, client, ui, expect, commit_before_loss=True)
                        verify_training_and_transfer(browser, client, ui, expect)
                        verify_storage_failure(browser, client, ui, expect)
                    finally:
                        browser.close()
    print("Writing D4 browser scenarios completed.")


if __name__ == "__main__":
    main()
