"""Tests for ConfigManager: comment-preserving round-trip and runtime hot-reload."""

import errno
import json
import os
from datetime import datetime, timezone
from unittest.mock import Mock, patch

import pytest

from app.config import ConfigurationError
from app.config.environment import EnvironmentConfig
from app.config.manager import ConfigManager, ConfigReloadResult, ConfigUpdatePayload
from app.config.models import FilteringRules, SearchProfile
from app.domain.models import Job
from app.notifications.service import NotificationService
from app.persistence.database import close_database, get_session, init_database
from app.persistence.repositories import JobRepository
from app.pipeline import ScanPipeline
from app.scheduler.service import SchedulerService

HEADER = "# Top-level document header"
WILDCARD_COMMENT = "# All Ashby companies"
FILTER_COMMENT = "# Filtering section comment"

COMMENTED_CONFIG = f"""\
{HEADER}
sources:
  - name: "Explicit"
    type: "greenhouse"
    identifier: "acme"
    enabled: true
  - name: "All Ashby"
    type: "ashby"
    identifier: "*"   {WILDCARD_COMMENT}
    enabled: true

scan_interval: "15m"

{FILTER_COMMENT}
filtering:
  max_job_age_days: 90

search_profiles:
  - name: "old"
    required_terms: ["python"]
"""

LEGACY_CONFIG = """\
# legacy config
sources:
  - name: "Explicit"
    type: "greenhouse"
    identifier: "acme"
search_criteria:
  required_terms: ["python"]
scan_interval: "15m"
"""


@pytest.fixture
def registry_dir(tmp_path):
    path = tmp_path / "registries"
    path.mkdir()
    for ats, companies in {
        "ashby": [
            {"identifier": "a1", "name": "A One", "enabled": True},
            {"identifier": "a2", "name": "A Two", "enabled": True},
            {"identifier": "a3", "name": "A Three", "enabled": False},
        ],
        "greenhouse": [],
        "lever": [],
    }.items():
        (path / f"{ats}_companies.json").write_text(
            json.dumps({"version": "1.0", "updated_at": "2025-01-01T00:00:00Z", "companies": companies})
        )
    return path


def _write(tmp_path, text):
    path = tmp_path / "config.yaml"
    path.write_text(text)
    return path


def _profiles(*names, term="rust"):
    return [SearchProfile(name=n, required_terms=[term]) for n in names]


class TestConfigManagerRoundTrip:
    def test_update_search_profiles_preserves_yaml_comments_and_wildcard_sources(
        self, tmp_path, registry_dir
    ):
        path = _write(tmp_path, COMMENTED_CONFIG)
        manager = ConfigManager(path, registry_dir=registry_dir)

        result = manager.update_config(ConfigUpdatePayload(search_profiles=_profiles("new")))

        text = path.read_text()
        assert isinstance(result, ConfigReloadResult)
        assert HEADER in text
        assert WILDCARD_COMMENT in text
        assert FILTER_COMMENT in text
        assert "rust" in text and "python" not in text
        raw = manager.get_raw_config()
        assert [s.identifier for s in raw.sources] == ["acme", "*"]
        assert [p.name for p in raw.search_profiles] == ["new"]
        expanded = manager.get_expanded_config()
        assert [s.identifier for s in expanded.sources] == ["acme", "a1", "a2"]
        assert result.expanded_config.sources == expanded.sources
        assert not list(tmp_path.glob("*.tmp"))

    def test_untouched_sections_keep_their_values(self, tmp_path, registry_dir):
        path = _write(tmp_path, COMMENTED_CONFIG)
        manager = ConfigManager(path, registry_dir=registry_dir)

        manager.update_config(ConfigUpdatePayload(scan_interval="30m"))

        raw = manager.get_raw_config()
        assert raw.scan_interval == "30m"
        assert raw.filtering.max_job_age_days == 90
        assert [p.name for p in raw.search_profiles] == ["old"]
        assert HEADER in path.read_text()

    def test_saving_search_profiles_migrates_legacy_search_criteria_key(
        self, tmp_path, registry_dir
    ):
        path = _write(tmp_path, LEGACY_CONFIG)
        manager = ConfigManager(path, registry_dir=registry_dir)

        manager.update_config(ConfigUpdatePayload(search_profiles=_profiles("migrated")))

        text = path.read_text()
        assert "search_criteria" not in text
        assert "search_profiles" in text
        assert "# legacy config" in text
        assert [p.name for p in ConfigManager(path, registry_dir=registry_dir).get_raw_config().search_profiles] == [
            "migrated"
        ]

    @pytest.mark.parametrize(
        "payload",
        [
            ConfigUpdatePayload(search_profiles=_profiles("dup", "dup")),
            ConfigUpdatePayload(scan_interval="1m"),
            ConfigUpdatePayload(scan_interval="soon"),
        ],
        ids=["duplicate-profile-names", "interval-too-short", "interval-unparseable"],
    )
    def test_invalid_config_update_raises_configuration_error_and_leaves_disk_and_runtime_untouched(
        self, tmp_path, registry_dir, payload
    ):
        path = _write(tmp_path, COMMENTED_CONFIG)
        before_bytes = path.read_bytes()
        pipeline = Mock()
        pipeline.app_config = "unchanged"
        manager = ConfigManager(path, pipeline=pipeline, registry_dir=registry_dir)
        before_config = manager.get_raw_config()

        with pytest.raises(ConfigurationError):
            manager.update_config(payload)

        assert path.read_bytes() == before_bytes
        assert not list(tmp_path.glob("*.tmp"))
        assert pipeline.app_config == "unchanged"
        assert manager.get_raw_config() == before_config

    def test_missing_config_file_raises_configuration_error(self, tmp_path):
        with pytest.raises(ConfigurationError):
            ConfigManager(tmp_path / "nope.yaml").get_raw_config()


class TestConfigManagerHotReload:
    @pytest.fixture(autouse=True)
    def database(self):
        init_database("sqlite:///:memory:")
        yield
        close_database()

    @staticmethod
    def _job(external_id, description):
        from app.utils.hashing import compute_content_hash, compute_job_key

        now = datetime.now(timezone.utc)
        return Job(
            job_key=compute_job_key("greenhouse", "acme", external_id),
            source_type="greenhouse",
            source_identifier="acme",
            external_id=external_id,
            title="Engineer",
            company="Acme",
            location="Remote",
            description=description,
            url="https://example.com/1",
            first_seen_at=now,
            last_seen_at=now,
            content_hash=compute_content_hash("Engineer", description, "Remote"),
        )

    def _wire(self, tmp_path, registry_dir):
        from app.matching.engine import KeywordMatcher

        path = _write(tmp_path, COMMENTED_CONFIG)
        manager = ConfigManager(path, registry_dir=registry_dir)
        config = manager.get_expanded_config()
        env = EnvironmentConfig(
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_user="u@example.com",
            smtp_pass="pw",
            alert_to_email="a@example.com",
            database_url="sqlite:///:memory:",
        )
        pipeline = ScanPipeline(
            app_config=config,
            env_config=env,
            notification_service=Mock(spec=NotificationService),
            keyword_matcher=KeywordMatcher(config.get_effective_profiles()),
        )
        scheduler = SchedulerService(Mock(), config.scan_interval_seconds)
        manager = ConfigManager(
            path, pipeline=pipeline, scheduler_service=scheduler, registry_dir=registry_dir
        )
        return manager, pipeline, scheduler

    def test_hot_reload_updates_pipeline_matcher_scheduler_and_reevaluates_stored_jobs(
        self, tmp_path, registry_dir
    ):
        manager, pipeline, scheduler = self._wire(tmp_path, registry_dir)
        with get_session() as session:
            repo = JobRepository(session)
            repo.upsert(self._job("1", "We use rust daily"))
            repo.upsert(self._job("2", "We use python daily"))
            repo.update_user_status(self._job("1", "").job_key, "saved")

        result = manager.update_config(
            ConfigUpdatePayload(search_profiles=_profiles("rusty"), scan_interval="30m")
        )

        assert result.reevaluated_jobs_count == 2
        assert [p.name for p in pipeline.keyword_matcher.profiles] == ["rusty"]
        assert pipeline.app_config.scan_interval == "30m"
        assert [s.identifier for s in pipeline.app_config.sources] == ["acme", "a1", "a2"]
        assert scheduler.interval_seconds == 1800
        with get_session() as session:
            repo = JobRepository(session)
            rust = repo.get_by_key(self._job("1", "").job_key)
            python = repo.get_by_key(self._job("2", "").job_key)
        assert rust is not None and python is not None
        assert rust.is_match is True and rust.primary_profile == "rusty"
        assert rust.user_status.value == "saved"
        assert python.is_match is False

    def test_non_profile_update_does_not_reevaluate(self, tmp_path, registry_dir):
        manager, pipeline, scheduler = self._wire(tmp_path, registry_dir)
        with get_session() as session:
            JobRepository(session).upsert(self._job("1", "rust"))

        result = manager.update_config(ConfigUpdatePayload(scan_interval="20m"))

        assert result.reevaluated_jobs_count == 0
        assert scheduler.interval_seconds == 1200
        with get_session() as session:
            job = JobRepository(session).get_by_key(self._job("1", "").job_key)
        assert job is not None and job.is_match is None

    def test_update_fails_without_touching_disk_when_scan_holds_the_lock(
        self, tmp_path, registry_dir
    ):
        manager, pipeline, _ = self._wire(tmp_path, registry_dir)
        manager.lock_timeout = 0.05
        path = tmp_path / "config.yaml"
        before = path.read_bytes()

        with pipeline._lock:
            with pytest.raises(ConfigurationError):
                manager.update_config(ConfigUpdatePayload(scan_interval="30m"))

        assert path.read_bytes() == before

    def test_reload_registries_reexpands_wildcards_into_pipeline(self, tmp_path, registry_dir):
        from app.adapters.registry import CompanyRegistry

        manager, pipeline, _ = self._wire(tmp_path, registry_dir)
        CompanyRegistry(registry_dir).add_company("ashby", "a4", "A Four")

        expanded = manager.reload_registries()

        ids = [s.identifier for s in expanded.sources]
        assert ids == ["acme", "a1", "a2", "a4"]
        assert [s.identifier for s in pipeline.app_config.sources] == ids


PARTIAL_CONFIG = """\
sources:
  - name: "Explicit"
    type: "greenhouse"
    identifier: "acme"
scan_interval: "15m"

# Guardrails: hand written
filtering:
  max_job_age_days: 90
  require_remote: true
  # keep these countries
  acceptable_countries:
    - "united states"
    - "canada"
  blocked_domains:
    - "spam.example"
search_profiles:
  - name: "old"
    required_terms: ["python"]
"""


class TestAtomicWriteFallback:
    @pytest.mark.parametrize("code", [errno.EBUSY, errno.EXDEV])
    def test_bind_mount_replace_failure_falls_back_to_in_place_write(
        self, tmp_path, registry_dir, code
    ):
        path = _write(tmp_path, COMMENTED_CONFIG)
        inode = os.stat(path).st_ino
        manager = ConfigManager(path, registry_dir=registry_dir)

        with patch("os.replace", side_effect=OSError(code, "Device or resource busy")):
            manager.update_config(ConfigUpdatePayload(search_profiles=_profiles("new")))

        text = path.read_text()
        assert "rust" in text and "python" not in text
        assert HEADER in text and WILDCARD_COMMENT in text
        assert os.stat(path).st_ino == inode
        assert not list(tmp_path.glob("*.tmp"))
        assert [p.name for p in manager.get_raw_config().search_profiles] == ["new"]

    def test_other_oserrors_still_fail_and_leave_file_untouched(self, tmp_path, registry_dir):
        path = _write(tmp_path, COMMENTED_CONFIG)
        manager = ConfigManager(path, registry_dir=registry_dir)

        with patch("os.replace", side_effect=OSError(errno.EACCES, "denied")):
            with pytest.raises(ConfigurationError):
                manager.update_config(ConfigUpdatePayload(search_profiles=_profiles("new")))

        assert path.read_text() == COMMENTED_CONFIG
        assert not list(tmp_path.glob("*.tmp"))

    def test_registry_save_falls_back_in_place(self, tmp_path):
        from app.adapters.registry import CompanyRegistry

        reg_dir = tmp_path / "registries"
        reg_dir.mkdir()
        target = reg_dir / "greenhouse_companies.json"
        target.write_text(json.dumps({"version": "1.0", "companies": []}))
        registry = CompanyRegistry(reg_dir)

        with patch("os.replace", side_effect=OSError(errno.EBUSY, "busy")):
            registry.save_companies("greenhouse", [{"identifier": "g1", "name": "G1"}])

        saved = json.loads(target.read_text())
        assert [c["identifier"] for c in saved["companies"]] == ["g1"]
        assert not list(reg_dir.glob("*.tmp"))


class TestPartialSectionMerge:
    def test_partial_filtering_update_keeps_unmentioned_keys_and_comments(
        self, tmp_path, registry_dir
    ):
        path = _write(tmp_path, PARTIAL_CONFIG)
        manager = ConfigManager(path, registry_dir=registry_dir)

        manager.update_config(
            ConfigUpdatePayload(filtering=FilteringRules(require_remote=False))
        )

        text = path.read_text()
        assert "# Guardrails: hand written" in text
        assert "# keep these countries" in text
        raw = manager.get_raw_config()
        assert raw.filtering.require_remote is False
        assert raw.filtering.max_job_age_days == 90
        assert raw.filtering.acceptable_countries == ["united states", "canada"]
        assert raw.filtering.blocked_domains == ["spam.example"]
