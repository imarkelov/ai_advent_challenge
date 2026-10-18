import asyncio

from events import EventBus
from pipeline.artifacts import (RunDir, review_markdown, sdd_text, skeleton_markdown,
                                test_report_markdown)
from store import RunStore


def test_create_run_writes_file(tmp_path):
    store = RunStore(str(tmp_path))
    run = store.create_run("TC-4217", "C:/repo", "feature/x")
    assert run["stage"] == "sdd"
    assert run["status"] == "running"
    assert (tmp_path / "runs" / f"{run['id']}.json").exists()


def test_update_and_get_run(tmp_path):
    store = RunStore(str(tmp_path))
    run = store.create_run("TC-4217", "C:/repo", "develop")
    store.update_run(run["id"], stage="study")
    assert store.get_run(run["id"])["stage"] == "study"


def test_get_missing_run_returns_none(tmp_path):
    assert RunStore(str(tmp_path)).get_run("nope") is None


def test_list_runs_newest_first(tmp_path):
    store = RunStore(str(tmp_path))
    first = store.create_run("TC-1", "C:/r", "a")
    second = store.create_run("TC-2", "C:/r", "b")
    ids = [run["id"] for run in store.list_runs()]
    assert ids.index(second["id"]) < ids.index(first["id"])


def test_append_log_is_capped(tmp_path):
    store = RunStore(str(tmp_path))
    run = store.create_run("TC-1", "C:/r", "a")
    for i in range(510):
        store.append_log(run["id"], "researcher", f"step {i}")
    assert len(store.get_run(run["id"])["log"]) == 500


def test_save_run_never_contains_secret(tmp_path):
    store = RunStore(str(tmp_path))
    run = store.create_run("TC-1", "C:/r", "a")
    raw = (tmp_path / "runs" / f"{run['id']}.json").read_text(encoding="utf-8")
    assert "api_key" not in raw


def test_event_bus_publish_and_subscribe():
    bus = EventBus()

    async def scenario():
        received = []

        async def reader():
            async for event in bus.subscribe("run1"):
                received.append(event)

        task = asyncio.create_task(reader())
        await asyncio.sleep(0)
        bus.publish("run1", {"type": "stage_changed", "stage": "study"})
        bus.close("run1")
        await asyncio.wait_for(task, timeout=2)
        return received

    assert asyncio.run(scenario()) == [{"type": "stage_changed", "stage": "study"}]


def test_event_bus_question_blocks_until_answer():
    bus = EventBus()

    async def scenario():
        bus.register_question("q1")
        waiter = asyncio.create_task(bus.wait_for_answer("q1", timeout=2))
        await asyncio.sleep(0)
        assert bus.answer("q1", "экран AddNewAddress")
        return await waiter

    assert asyncio.run(scenario()) == "экран AddNewAddress"


def test_event_bus_question_timeout_returns_none():
    bus = EventBus()

    async def scenario():
        bus.register_question("q2")
        return await bus.wait_for_answer("q2", timeout=0.01)

    assert asyncio.run(scenario()) is None


def test_run_dir_layout_and_write(tmp_path):
    run_dir = RunDir(str(tmp_path / "doc"), "r1")
    run_dir.ensure()
    assert (tmp_path / "doc" / "runs" / "r1" / "logs").is_dir()
    run_dir.write("sdd/agent_researcher.md", "# SDD")
    assert run_dir.read("sdd/agent_researcher.md") == "# SDD"
    assert run_dir.skeleton_path.name == "skeleton.md"
    assert run_dir.review_path.name == "review_report.md"


def test_sdd_text_fills_fields():
    text = sdd_text(role="researcher", task_id="TC-4217", task_title="Адрес",
                    repo_path="C:/repo", branch="develop", git_summary="- abc first",
                    goal="Изучить экран", rag_inputs="kb/architecture.md",
                    artifacts="—", instructions="1. Изучи экран",
                    allowed_tools="rag.search", max_rounds=2, write_scope="kb/",
                    criteria="- [ ] экран найден")
    assert text.startswith("# SDD — researcher")
    assert "TC-4217" in text
    assert "max_rounds: 2" in text


def test_skeleton_markdown_has_all_sections():
    md = skeleton_markdown("AddNewAddressTest", "TC-4217",
                           ["Открыть экран"], ["AddressAddUIConstants.AcceptButton"],
                           ["адрес"], ["валидация сервера"], ["—"])
    for section in ("# Каркас — AddNewAddressTest", "## Шаги", "## Используемые теги",
                    "## Входные данные", "## Границы", "## Ожидаемые правки"):
        assert section in md


def test_review_markdown_table_and_verdict():
    md = review_markdown("run1", [{"test": "AddTest", "status": "passed",
                                   "duration": "12s", "reason": ""}], "green")
    assert "| AddTest | passed | 12s |" in md
    assert "**Вердикт:** green" in md


def test_test_report_markdown_lists_stages():
    md = test_report_markdown("run1", "TC-4217", "develop",
                              ["sdd", "study", "done"], "green", ["a.md"], "готово")
    assert "sdd → study → done" in md
    assert "Вердикт: green" in md
