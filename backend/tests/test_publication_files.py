import json
from types import SimpleNamespace

import pytest
import yaml

from baseline_hub.catalog import CatalogModel
from baseline_hub.publishing import PublishingError


def test_target_metadata_versions_are_preserved_and_validation_sidecar_is_omitted():
    from baseline_hub.publication_files import prepare_files

    old = {
        "apiVersion": "catalog.swiss/v2",
        "kind": "Variant",
        "model": "demo",
        "id": "fast",
        "version": "1.4.0",
        "description": "old",
    }
    new = old | {"version": "1.0.0", "description": "new"}
    meta = {
        "apiVersion": "catalog.swiss/v2",
        "name": "demo",
        "displayName": "Keep me",
        "source": {"hf": "Org/Model"},
        "tuning": [{"optimized": "other@1.0.0"}],
    }
    exported_meta = meta | {
        "displayName": "New default",
        "tuning": [{"baseline": "base@1.0.0", "optimized": "fast@1.0.0", "report": "r.html"}],
    }
    exported = {
        "name": "demo",
        "valid": True,
        "metadata": exported_meta,
        "provenance": [],
        "files": [
            {
                "path": "models/demo/metadata.yaml",
                "kind": "metadata",
                "text": yaml.safe_dump(exported_meta),
            },
            {"path": "models/demo/fast.yaml", "kind": "variant", "text": yaml.safe_dump(new)},
            {
                "path": "models/demo/demo.validation.json",
                "kind": "validation",
                "text": json.dumps(
                    {
                        "schema_version": 2,
                        "model": "demo",
                        "variants": [
                            {"variant_id": "fast", "version": "1.0.0", "file": "fast.yaml"}
                        ],
                    }
                ),
            },
        ],
    }
    model = CatalogModel(name="demo", metadata=meta, metadata_text=yaml.safe_dump(meta))
    model.variants["fast"] = {"version": "1.4.0", "document": old}
    github = SimpleNamespace(model=lambda *args: model, blob=lambda sha: yaml.safe_dump(old))
    # Existing target causes a fresh remote export with the target model identity.
    http = SimpleNamespace(
        request=lambda *args, **kw: SimpleNamespace(status_code=200, json=lambda: exported)
    )
    files, _, _ = prepare_files(github, {"paths": {}}, exported, {"hf": "Org/Model"}, http)
    by_kind = {f["kind"]: f for f in files}
    variant = yaml.safe_load(by_kind["variant"]["text"])
    assert variant["version"] == "1.5.0"
    merged = yaml.safe_load(by_kind["metadata"]["text"])
    assert merged["displayName"] == "Keep me"
    assert merged["tuning"][0] == meta["tuning"][0]
    assert merged["tuning"][1]["optimized"] == "fast@1.5.0"
    assert "validation" not in by_kind


def test_outside_model_export_is_refused():
    from baseline_hub.publication_files import prepare_files

    exported = {
        "name": "demo",
        "valid": True,
        "metadata": {},
        "files": [{"path": ".github/workflows/run.yml", "kind": "report", "text": "bad"}],
    }
    github = SimpleNamespace(model=lambda *args: None)
    with pytest.raises(PublishingError):
        prepare_files(github, {"paths": {}}, exported, {}, None)


def test_existing_default_variant_is_checked_against_target_not_new_export():
    from baseline_hub.publication_files import prepare_files

    meta = {"apiVersion": "catalog.swiss/v2", "source": {"hf": "Org/Model"}}
    existing = CatalogModel(name="demo", metadata=meta, metadata_text=yaml.safe_dump(meta))
    existing.variants["published"] = {"version": "1.0.0", "document": {}}
    exported = {
        "name": "demo",
        "valid": True,
        "metadata": meta,
        "files": [
            {"path": "models/demo/metadata.yaml", "kind": "metadata", "text": yaml.safe_dump(meta)}
        ],
    }
    github = SimpleNamespace(model=lambda *args: existing)

    def request(*args, **kwargs):
        # A default already present on GitHub must not be validated as a new model ID.
        assert kwargs["json"].get("default_variant", "") == ""
        return SimpleNamespace(status_code=200, json=lambda: exported)

    http = SimpleNamespace(request=request)
    files, _, _ = prepare_files(
        github, {"paths": {}}, exported, {"default_variant": "published"}, http
    )
    assert yaml.safe_load(files[0]["text"])["defaultVariant"] == "published"
