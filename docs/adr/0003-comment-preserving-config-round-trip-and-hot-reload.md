---
status: accepted
---

# Comment-Preserving YAML Round-Trip and Lock-Synchronized Hot-Reload

At startup, `load_config()` expands `identifier: "*"` Wildcard Sources into hundreds of concrete `SourceConfig` entries from `data/registries/*.json` and strips YAML comments. Serializing the in-memory `AppConfig` back to `config.yaml` via `yaml.safe_dump()` would destroy user documentation comments and permanently overwrite wildcard definitions with expanded company lists. We therefore use `ruamel.yaml` in round-trip mode to patch only modified top-level keys on the raw `config.yaml` AST before wildcard expansion, validate the candidate document via `AppConfig.model_validate()`, write atomically via `.tmp` + `os.replace()`, and hot-reload `pipeline.app_config`, `pipeline.keyword_matcher`, and `SchedulerService` under `ScanPipeline._lock`.

## Consequences

- When saving search profiles from the UI on a legacy config that only defines `search_criteria`, `ConfigManager` migrates the rule set to `search_profiles` and removes the legacy `search_criteria` key while keeping surrounding comments intact.
- In-flight scans are protected because hot-reload acquires `ScanPipeline._lock` (or waits briefly for completion) before swapping `app_config`, `keyword_matcher`, and rescheduling the APScheduler interval trigger.
