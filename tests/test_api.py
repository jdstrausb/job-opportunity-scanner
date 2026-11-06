"""Integration tests for the FastAPI bridge, driven through ``TestClient``."""

import json
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient

from app.adapters.exceptions import AdapterHTTPError
from app.api import create_app
from app.config.environment import EnvironmentConfig
from app.config.manager import ConfigManager
from app.domain.models import Job, RawJob
from app.matching.engine import KeywordMatcher
from app.notifications.service import NotificationService
from app.persistence.database import close_database, get_session, init_database
from app.persistence.repositories import AlertRepository, JobRepository, SourceRepository
from app.pipeline import ScanPipeline
from app.scheduler.service import SchedulerService
from app.utils.hashing import compute_content_hash, compute_job_key

CONFIG_YAML = """\
# API test config
sources:
  - name: "All Greenhouse"
    type: "greenhouse"
    identifier: "*"
  - name: "Explicit Lever"
    type: "lever"
    identifier: "acme-lever"
scan_interval: "15m"
search_profiles:
  - name: "py"
    required_terms: ["python"]
"""

SMTP_SECRET = "super-secret-smtp-pass"
SERP_SECRET = "serp-secret-key-123"


def _registry(path, companies_by_ats):
    path.mkdir()
    for ats in ("greenhouse", "lever", "ashby"):
        (path / f"{ats}_companies.json").write_text(
            json.dumps(
                {
                    "version": "1.0",
                    "updated_at": "2025-01-01T00:00:00Z",
                    "companies": companies_by_ats.get(ats, []),
                }
            )
        )


@pytest.fixture
def api(tmp_path):
    init_database(f"sqlite:///{tmp_path / 'api.db'}")
    registry_dir = tmp_path / "registries"
    _registry(
        registry_dir,
        {
            "greenhouse": [
                {"identifier": "g1", "name": "Gamma One", "enabled": True},
                {"identifier": "g2", "name": "Alpha Two", "enabled": True},
                {"identifier": "g3", "name": "Zeta Three", "enabled": False},
            ]
        },
    )
    config_path = tmp_path / "config.yaml"
    config_path.write_text(CONFIG_YAML)

    manager = ConfigManager(config_path, registry_dir=registry_dir)
    config = manager.get_expanded_config()
    env = EnvironmentConfig(
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_user="user@example.com",
        smtp_pass=SMTP_SECRET,
        alert_to_email="alerts@example.com",
        database_url=f"sqlite:///{tmp_path / 'api.db'}",
    )
    notifier = Mock(spec=NotificationService)
    pipeline = ScanPipeline(
        app_config=config,
        env_config=env,
        notification_service=notifier,
        keyword_matcher=KeywordMatcher(config.get_effective_profiles()),
    )
    scheduler = SchedulerService(Mock(), 900)
    manager = ConfigManager(
        config_path,
        pipeline=pipeline,
        scheduler_service=scheduler,
        registry_dir=registry_dir,
    )
    client = TestClient(create_app(pipeline, scheduler, manager))
    yield SimpleNamespace(
        client=client,
        pipeline=pipeline,
        scheduler=scheduler,
        manager=manager,
        notifier=notifier,
        config_path=config_path,
        registry_dir=registry_dir,
        env=env,
    )
    close_database()


def make_job(external_id, source="greenhouse", source_id="acme", **overrides):
    now = datetime.now(timezone.utc)
    title = overrides.pop("title", "Software Engineer")
    description = overrides.pop("description", "Generic description")
    location = overrides.pop("location", "Remote")
    return Job(
        job_key=compute_job_key(source, source_id, external_id),
        source_type=source,
        source_identifier=source_id,
        external_id=external_id,
        title=title,
        company=overrides.pop("company", "Acme"),
        location=location,
        description=description,
        url=f"https://example.com/{external_id}",
        posted_at=overrides.pop("posted_at", now),
        first_seen_at=now,
        last_seen_at=now,
        content_hash=compute_content_hash(title, description, location),
        **overrides,
    )


@pytest.fixture
def seeded(api):
    """a: matched/alerted, b: matched/pending, c: unmatched."""
    now = datetime.now(timezone.utc)
    a = make_job(
        "a",
        title="Backend Engineer",
        description="Senior Python backend engineer",
        is_match=True,
        primary_profile="backend",
        matched_profiles=["backend"],
        affinity_score=9.0,
        score_breakdown={"python": 9.0},
        match_snippets=["Senior **Python** backend"],
        matched_fields={"description": ["python"]},
        is_remote=True,
        posted_at=now - timedelta(days=10),
    )
    b = make_job(
        "b",
        title="Data Engineer",
        company="Globex",
        source_id="globex",
        description="Data engineer using Python and SQL",
        is_match=True,
        primary_profile="data",
        matched_profiles=["data", "backend"],
        affinity_score=5.0,
        is_remote=False,
        posted_at=now - timedelta(days=1),
    )
    c = make_job("c", title="Office Manager", description="Run the office", is_match=False)
    with get_session() as session:
        repo = JobRepository(session)
        for job in (a, b, c):
            repo.upsert(job)
        AlertRepository(session).record_alert(a.job_key, a.content_hash, now)
    return SimpleNamespace(a=a, b=b, c=c)


def keys(response):
    return [item["job_key"] for item in response.json()["items"]]


class TestJobsRoutes:
    def test_jobs_triage_list_detail_and_status_transitions(self, api, seeded):
        client = api.client

        page = client.get("/api/jobs")
        assert page.status_code == 200
        body = page.json()
        assert keys(page) == [seeded.a.job_key, seeded.b.job_key]  # affinity desc
        assert (body["total"], body["limit"], body["offset"]) == (2, 50, 0)
        assert body["counts"] == {"matched": 2, "saved": 0, "applied": 0, "pending_alert": 1}
        first = body["items"][0]
        assert first["alert_status"] == "alerted" and first["alert_sent_at"] is not None
        assert first["primary_profile"] == "backend"
        assert first["user_status"] == "new"
        assert body["items"][1]["alert_status"] == "pending_alert"

        assert set(keys(client.get("/api/jobs", params={"match_mode": "all"}))) == {
            seeded.a.job_key,
            seeded.b.job_key,
            seeded.c.job_key,
        }
        assert keys(client.get("/api/jobs", params={"match_mode": "unmatched"})) == [seeded.c.job_key]
        assert keys(client.get("/api/jobs", params={"profile": "data"})) == [seeded.b.job_key]
        assert keys(client.get("/api/jobs", params={"is_remote": "true"})) == [seeded.a.job_key]
        assert keys(client.get("/api/jobs", params={"alert_status": "pending_alert"})) == [
            seeded.b.job_key
        ]
        assert keys(client.get("/api/jobs", params={"alert_status": "alerted"})) == [seeded.a.job_key]
        assert keys(client.get("/api/jobs", params={"q": "SQL"})) == [seeded.b.job_key]
        assert keys(client.get("/api/jobs", params={"source_identifier": "globex"})) == [
            seeded.b.job_key
        ]
        assert keys(
            client.get("/api/jobs", params={"sort_by": "posted_at", "sort_order": "desc"})
        ) == [seeded.b.job_key, seeded.a.job_key]
        paged = client.get("/api/jobs", params={"limit": 1, "offset": 1})
        assert keys(paged) == [seeded.b.job_key]
        assert paged.json()["total"] == 2

        for bad in ({"match_mode": "some"}, {"limit": 0}, {"limit": 500}, {"sort_by": "title"},
                    {"user_status": "bogus"}, {"alert_status": "none"}):
            assert client.get("/api/jobs", params=bad).status_code == 422

        detail = client.get(f"/api/jobs/{seeded.a.job_key}")
        assert detail.status_code == 200
        assert detail.json()["description"] == "Senior Python backend engineer"
        assert detail.json()["match_snippets"] == ["Senior **Python** backend"]
        assert detail.json()["matched_fields"] == {"description": ["python"]}
        assert detail.json()["score_breakdown"] == {"python": 9.0}
        assert client.get("/api/jobs/nope").status_code == 404

        url = f"/api/jobs/{seeded.b.job_key}/status"
        saved = client.patch(url, json={"user_status": "saved"})
        assert saved.status_code == 200 and saved.json()["user_status"] == "saved"
        assert keys(client.get("/api/jobs", params={"user_status": "saved"})) == [seeded.b.job_key]
        assert client.get("/api/jobs").json()["counts"]["saved"] == 1
        applied = client.patch(url, json={"user_status": "applied"})
        assert applied.json()["user_status"] == "applied"
        assert client.patch(url, json={"user_status": "bogus"}).status_code == 422
        assert client.patch(url, json={}).status_code == 422
        assert client.patch("/api/jobs/nope/status", json={"user_status": "saved"}).status_code == 404


class TestDismissalRoutes:
    def test_dismissals_create_query_time_hide_undo_and_list(self, api, seeded):
        client = api.client

        job = client.post(
            "/api/dismissals",
            json={"scope": "job", "item_key": seeded.a.job_key, "reason": "not for me"},
        )
        assert job.status_code == 201
        assert job.json()["hidden_job_count"] == 1
        assert job.json()["item"]["scope"] == "job"
        assert job.json()["item"]["reason"] == "not for me"
        assert keys(client.get("/api/jobs")) == [seeded.b.job_key]

        company = client.post("/api/dismissals", json={"scope": "company", "item_key": " GLOBEX "})
        assert company.status_code == 201
        assert company.json()["hidden_job_count"] == 1
        assert company.json()["item"]["item_key"] == "globex"
        assert keys(client.get("/api/jobs")) == []
        assert client.get("/api/jobs").json()["counts"]["matched"] == 0

        source = client.post("/api/dismissals", json={"scope": "source", "item_key": "acme"})
        assert source.json()["hidden_job_count"] == 2  # a and c

        listed = client.get("/api/dismissals", params={"scope": "company"})
        assert listed.status_code == 200
        assert listed.json()["total"] == 1
        assert listed.json()["items"][0]["item_key"] == "globex"
        assert client.get("/api/dismissals").json()["total"] == 3
        assert client.get("/api/dismissals", params={"q": "not for"}).json()["total"] == 1

        with get_session() as session:  # rows were never deleted
            assert len(JobRepository(session).get_by_source("greenhouse", "acme")) == 2

        client.patch(f"/api/jobs/{seeded.b.job_key}/status", json={"user_status": "saved"})
        restored = client.delete("/api/dismissals", params={"scope": "company", "item_key": "Globex"})
        assert restored.status_code == 200
        assert restored.json() == {"restored": True, "scope": "company", "item_key": "globex"}
        back = client.get("/api/jobs")
        assert keys(back) == [seeded.b.job_key]
        assert back.json()["items"][0]["user_status"] == "saved"

        assert client.delete("/api/dismissals", params={"scope": "company", "item_key": "globex"}).status_code == 404
        assert client.post("/api/dismissals", json={"scope": "bogus", "item_key": "x"}).status_code == 422
        assert client.post("/api/dismissals", json={"scope": "job", "item_key": ""}).status_code == 422


class TestConfigRoutes:
    def test_config_get_preview_and_put_hot_reload(self, api, seeded):
        client = api.client

        config = client.get("/api/config")
        assert config.status_code == 200
        body = config.json()
        assert [s["identifier"] for s in body["sources"]] == ["*", "acme-lever"]  # unexpanded
        assert [p["name"] for p in body["search_profiles"]] == ["py"]
        assert body["scan_interval"] == "15m"
        assert "filtering" in body and "email" in body
        assert body["env_status"] == {"serpapi_configured": False, "smtp_configured": True}
        assert SMTP_SECRET not in config.text
        assert "smtp_pass" not in config.text

        before_jobs = client.get("/api/jobs", params={"match_mode": "all"}).json()
        before_disk = api.config_path.read_bytes()

        preview = client.post(
            "/api/config/preview",
            json={"search_profiles": [{"name": "sql", "required_terms": ["sql"]}]},
        )
        assert preview.status_code == 200
        pv = preview.json()
        assert pv["total_evaluated"] == 3
        assert pv["total_matched"] == 1
        assert pv["newly_matched_count"] == 0
        assert pv["dropped_match_count"] == 1  # job a no longer matches
        assert pv["profile_counts"] == {"sql": 1}
        assert [m["job_key"] for m in pv["sample_matches"]] == [seeded.b.job_key]
        assert pv["sample_matches"][0]["primary_profile"] == "sql"
        assert client.get("/api/jobs", params={"match_mode": "all"}).json() == before_jobs
        assert api.config_path.read_bytes() == before_disk

        assert client.post("/api/config/preview", json={"search_profiles": []}).status_code == 422
        dup = client.post(
            "/api/config/preview",
            json={
                "search_profiles": [
                    {"name": "x", "required_terms": ["a"]},
                    {"name": "x", "required_terms": ["b"]},
                ]
            },
        )
        assert dup.status_code == 422 and dup.json()["errors"]
        malformed = client.post("/api/config/preview", json={"search_profiles": [{"name": "x"}]})
        assert malformed.status_code == 422 and malformed.json()["errors"]

        invalid = client.put(
            "/api/config",
            json={
                "search_profiles": [
                    {"name": "d", "required_terms": ["a"]},
                    {"name": "d", "required_terms": ["b"]},
                ]
            },
        )
        assert invalid.status_code == 422
        assert invalid.json()["errors"] and "detail" in invalid.json()
        assert client.put("/api/config", json={"scan_interval": "1m"}).status_code == 422
        assert api.config_path.read_bytes() == before_disk

        updated = client.put(
            "/api/config",
            json={
                "search_profiles": [
                    {"name": "sqlish", "required_terms": ["sql"]},
                ],
                "scan_interval": "30m",
            },
        )
        assert updated.status_code == 200
        assert updated.json()["reevaluated_jobs_count"] == 3
        assert [p["name"] for p in updated.json()["search_profiles"]] == ["sqlish"]
        assert [s["identifier"] for s in updated.json()["sources"]] == ["*", "acme-lever"]
        assert api.pipeline.keyword_matcher.profiles[0].name == "sqlish"
        assert api.scheduler.interval_seconds == 1800
        text = api.config_path.read_text()
        assert "# API test config" in text and '"*"' in text
        assert keys(client.get("/api/jobs")) == [seeded.b.job_key]
        assert client.get("/api/jobs").json()["items"][0]["primary_profile"] == "sqlish"


class TestSourcesAndRegistries:
    def test_sources_wildcard_rollup_registries_and_test_query(self, api):
        client = api.client
        at = datetime(2025, 5, 1, tzinfo=timezone.utc)
        with get_session() as session:
            repo = SourceRepository(session)
            repo.record_fetch("greenhouse", "g1", name="Gamma One", at=at)
            repo.record_fetch(
                "greenhouse",
                "g2",
                name="Alpha Two",
                at=at,
                error="boom https://x.example/api?api_key=SECRET123&b=1",
            )

        sources = client.get("/api/sources")
        assert sources.status_code == 200
        body = sources.json()
        assert [s["identifier"] for s in body["explicit_sources"]] == ["acme-lever"]
        assert body["explicit_sources"][0]["health_status"] == "unscanned"
        rollup = body["wildcard_rollups"][0]
        assert rollup["type"] == "greenhouse"
        assert {k: rollup[k] for k in (
            "total_companies", "total_enabled", "healthy_count", "error_count", "unscanned_count"
        )} == {
            "total_companies": 3,
            "total_enabled": 2,
            "healthy_count": 1,
            "error_count": 1,
            "unscanned_count": 0,
        }
        assert rollup["last_success_at"] is not None and rollup["last_error_at"] is not None
        assert [c["identifier"] for c in rollup["companies"]] == ["g2", "g1", "g3"]  # error first
        assert [c["health_status"] for c in rollup["companies"]] == ["error", "healthy", "disabled"]
        assert "SECRET123" not in sources.text
        assert "REDACTED" in rollup["companies"][0]["error_message"]

        reg = client.get("/api/registries/greenhouse")
        assert reg.status_code == 200
        assert reg.json()["total"] == 3 and reg.json()["enabled_count"] == 2
        assert reg.json()["updated_at"] == "2025-01-01T00:00:00Z"
        assert [c["identifier"] for c in client.get(
            "/api/registries/greenhouse", params={"q": "alpha"}).json()["companies"]] == ["g2"]
        assert len(client.get(
            "/api/registries/greenhouse", params={"enabled_only": "true"}).json()["companies"]) == 2
        assert client.get("/api/registries/workday").status_code == 422

        toggled = client.put(
            "/api/registries/greenhouse",
            json={"updates": [{"identifier": "g3", "enabled": True}]},
        )
        assert toggled.status_code == 200
        assert toggled.json()["enabled_count"] == 3
        assert toggled.json()["updated_at"] != "2025-01-01T00:00:00Z"
        assert toggled.json()["reloaded"] is True
        assert [s.identifier for s in api.pipeline.app_config.sources] == [
            "g1", "g2", "g3", "acme-lever",
        ]
        assert client.put(
            "/api/registries/greenhouse",
            json={"updates": [{"identifier": "missing", "enabled": True}]},
        ).status_code == 404
        replaced = client.put(
            "/api/registries/greenhouse",
            json={"companies": [{"identifier": "only", "name": "Only One"}]},
        )
        assert replaced.status_code == 200 and replaced.json()["total"] == 1
        assert client.put("/api/registries/greenhouse", json={}).status_code == 422
        assert client.put(
            "/api/registries/greenhouse",
            json={"companies": [{"identifier": "d", "name": "A"}, {"identifier": "D", "name": "B"}]},
        ).status_code == 422

        added = client.post(
            "/api/registries/greenhouse/companies", json={"identifier": "NewCo", "name": "New Co"}
        )
        assert added.status_code == 201
        assert added.json()["total"] == 2
        assert "newco" in [s.identifier.lower() for s in api.pipeline.app_config.sources]
        dup = client.post(
            "/api/registries/greenhouse/companies", json={"identifier": "newco", "name": "Dup"}
        )
        assert dup.status_code == 409

        missing_key = client.post("/api/sources/test-query", json={"query": "site:x"})
        assert missing_key.status_code == 400
        assert missing_key.json()["serpapi_configured"] is False

        api.env.serpapi_api_key = SERP_SECRET
        preview_jobs = [
            RawJob(external_id="h" * 64, title="T", company="C", description="d",
                   url="https://x.example/1"),
        ]
        with patch("app.api.routes.sources.SerpApiSearchAdapter") as adapter_cls:
            adapter_cls.return_value.fetch_jobs.return_value = preview_jobs
            ok = client.post("/api/sources/test-query", json={"query": "site:x", "num_results": 5})
            assert ok.status_code == 200
            assert ok.json()["count"] == 1 and ok.json()["query"] == "site:x"
            job = ok.json()["jobs"][0]
            assert job["title"] == "T" and job["url"] == "https://x.example/1"
            assert job["hydrated"] is True
            source_arg = adapter_cls.return_value.fetch_jobs.call_args.args[0]
            assert source_arg.identifier == "site:x" and source_arg.options["num_results"] == 5

            adapter_cls.return_value.fetch_jobs.side_effect = AdapterHTTPError(
                f"failed https://serpapi.com/search?api_key={SERP_SECRET}",
                status_code=500,
                url="https://serpapi.com/search",
            )
            bad = client.post("/api/sources/test-query", json={"query": "site:x"})
            assert bad.status_code == 502
            assert SERP_SECRET not in bad.text
        assert client.post("/api/sources/test-query", json={"query": ""}).status_code == 422


class TestScanRoutes:
    def test_scan_status_and_alert_safe_trigger_and_conflict(self, api):
        client = api.client
        idle = client.get("/api/scan/status")
        assert idle.status_code == 200
        assert idle.json() == {
            "is_running": False,
            "scan_interval": "15m",
            "scan_interval_seconds": 900,
            "next_run_time": None,
            "last_run_result": None,
        }

        raw = RawJob(
            external_id="job1",
            title="Engineer",
            company="Acme",
            location="Remote",
            description="We need a python developer",
            url="https://example.com/1",
        )
        with patch("app.pipeline.runner.get_adapter") as get_adapter:
            get_adapter.return_value.fetch_jobs.return_value = [raw]
            triggered = client.post("/api/scan/trigger")
            assert triggered.status_code == 202
            assert triggered.json() == {"status": "started", "send_notifications": False}
            deadline = time.time() + 10
            while (api.pipeline.last_run_result is None or api.pipeline.is_running) and (
                time.time() < deadline
            ):
                time.sleep(0.02)

        status = client.get("/api/scan/status").json()
        last = status["last_run_result"]
        assert status["is_running"] is False
        assert last["total_fetched"] == 3 and last["total_matched"] == 3
        assert last["total_notified"] == 0 and last["alerts_sent"] == 0
        assert last["skipped"] is False and last["had_errors"] is False
        assert len(last["source_stats"]) == 3
        api.notifier.send_digest.assert_not_called()
        api.notifier.send_notifications.assert_not_called()
        page = client.get("/api/jobs", params={"match_mode": "all"}).json()
        assert page["total"] == 3
        assert page["counts"]["pending_alert"] == 3
        assert {i["alert_status"] for i in page["items"]} == {"pending_alert"}

        with api.pipeline._lock:
            assert client.get("/api/scan/status").json()["is_running"] is True
            assert client.post("/api/scan/trigger").status_code == 409

    def test_trigger_passes_send_notifications_flag(self, api):
        api.pipeline.run_once = Mock()
        response = api.client.post("/api/scan/trigger", json={"send_notifications": True})
        assert response.status_code == 202
        assert response.json()["send_notifications"] is True
        deadline = time.time() + 5
        while not api.pipeline.run_once.called and time.time() < deadline:
            time.sleep(0.02)
        api.pipeline.run_once.assert_called_once_with(send_notifications=True)


class TestAppFactory:
    def test_config_routes_return_503_without_config_manager(self, api):
        client = TestClient(create_app(api.pipeline))
        assert client.get("/api/config").status_code == 503
        assert client.get("/api/scan/status").json()["next_run_time"] is None

    def test_scheduler_lifespan_is_opt_in(self, api):
        scheduler = Mock()
        with TestClient(create_app(api.pipeline, scheduler)):
            scheduler.start.assert_not_called()
        scheduler.shutdown.assert_not_called()

        with TestClient(create_app(api.pipeline, scheduler, manage_scheduler_lifespan=True)):
            scheduler.start.assert_called_once()
        scheduler.shutdown.assert_called_once_with(wait=False)

    def test_fastapi_lifespan_starts_and_stops_scheduler(self, api):
        scheduler = Mock()
        app = create_app(api.pipeline, scheduler_service=scheduler, manage_scheduler_lifespan=True)

        with TestClient(app):
            scheduler.start.assert_called_once_with()
            scheduler.shutdown.assert_not_called()

        scheduler.shutdown.assert_called_once_with(wait=False)

    def test_scheduler_is_stopped_even_if_the_app_fails_while_running(self, api):
        scheduler = Mock()
        app = create_app(api.pipeline, scheduler_service=scheduler, manage_scheduler_lifespan=True)

        with pytest.raises(RuntimeError):
            with TestClient(app):
                raise RuntimeError("boom")

        scheduler.shutdown.assert_called_once_with(wait=False)


class TestStaticSpa:
    @pytest.fixture
    def spa(self, api, tmp_path):
        root = tmp_path / "site"
        (root / "_app" / "immutable").mkdir(parents=True)
        (root / "index.html").write_text("<html>SPA SHELL</html>")
        (root / "_app" / "immutable" / "bundle.js").write_text("console.log('bundle')")
        (root / "favicon.png").write_bytes(b"PNGDATA")
        (tmp_path / "secret.txt").write_text("TOP SECRET")
        return SimpleNamespace(
            root=root, client=TestClient(create_app(api.pipeline, static_dir=root))
        )

    def test_static_spa_serving_and_client_route_fallback(self, spa):
        client = spa.client

        asset = client.get("/_app/immutable/bundle.js")
        assert asset.status_code == 200
        assert asset.text == "console.log('bundle')"

        for route in ("/", "/profiles", "/sources", "/jobs/some-key/deep/link"):
            page = client.get(route)
            assert page.status_code == 200, route
            assert page.text == "<html>SPA SHELL</html>"
            assert "text/html" in page.headers["content-type"]

        assert client.get("/favicon.png").content == b"PNGDATA"

        missing = client.get("/api/nonexistent")
        assert missing.status_code == 404
        assert missing.json() == {"detail": "Not Found", "errors": []}
        assert client.get("/api/jobs/nope").status_code == 404
        assert client.get("/api/scan/status").status_code == 200  # API still wins

    def test_missing_asset_under_app_dir_is_404_not_the_shell(self, spa):
        assert spa.client.get("/_app/immutable/missing.js").status_code == 404

    def test_static_serving_cannot_escape_the_static_directory(self, spa):
        for path in ("/../secret.txt", "/%2e%2e/secret.txt", "/..%2fsecret.txt"):
            response = spa.client.get(path)
            assert "TOP SECRET" not in response.text, path

    def test_health_payload_when_ui_is_not_built(self, api, tmp_path):
        no_dir = TestClient(create_app(api.pipeline))
        assert no_dir.get("/").json() == {"status": "ok", "ui_built": False}
        assert no_dir.get("/profiles").status_code == 404
        assert no_dir.get("/api/scan/status").status_code == 200

        empty = tmp_path / "empty"
        empty.mkdir()
        no_index = TestClient(create_app(api.pipeline, static_dir=empty))
        assert no_index.get("/").json() == {"status": "ok", "ui_built": False}
        assert no_index.get("/api/nonexistent").status_code == 404

        missing_dir = TestClient(create_app(api.pipeline, static_dir=tmp_path / "nope"))
        assert missing_dir.get("/").json() == {"status": "ok", "ui_built": False}


class TestHostHeaderValidation:
    def test_unknown_host_is_rejected(self, api):
        response = api.client.get("/api/scan/status", headers={"Host": "evil.com"})
        assert response.status_code == 400
        assert "Invalid host header" in response.text

    @pytest.mark.parametrize("host", ["localhost", "127.0.0.1:8000", "testserver"])
    def test_default_hosts_are_allowed(self, api, host):
        assert api.client.get("/api/scan/status", headers={"Host": host}).status_code == 200

    def test_allowed_hosts_env_adds_hostnames(self, api, monkeypatch):
        monkeypatch.setenv("ALLOWED_HOSTS", "scanner.lan")
        client = TestClient(create_app(api.pipeline, api.scheduler, api.manager))
        assert client.get("/api/scan/status", headers={"Host": "scanner.lan"}).status_code == 200
        assert client.get("/api/scan/status", headers={"Host": "evil.com"}).status_code == 400

    def test_wildcard_disables_the_check(self, api, monkeypatch):
        monkeypatch.setenv("ALLOWED_HOSTS", "*")
        client = TestClient(create_app(api.pipeline, api.scheduler, api.manager))
        assert client.get("/api/scan/status", headers={"Host": "evil.com"}).status_code == 200


class TestErrorEnvelope:
    @staticmethod
    def _assert_envelope(response, status):
        assert response.status_code == status
        body = response.json()
        assert isinstance(body["detail"], str)
        assert isinstance(body["errors"], list)

    def test_unknown_route_404(self, api):
        self._assert_envelope(api.client.get("/api/nonexistent"), 404)

    def test_scan_conflict_409(self, api):
        with api.pipeline._lock:
            self._assert_envelope(api.client.post("/api/scan/trigger"), 409)

    def test_bad_request_400(self, api):
        self._assert_envelope(
            api.client.post("/api/sources/test-query", json={"query": "site:x"}), 400
        )

    def test_duplicate_registry_company_409(self, api):
        payload = {"identifier": "newco", "name": "New Co"}
        api.client.post("/api/registries/greenhouse/companies", json=payload)
        self._assert_envelope(
            api.client.post("/api/registries/greenhouse/companies", json=payload), 409
        )

