"""Reconcile server-generated v2 exports against the exact GitHub review commit."""

import copy
import re

import yaml
from fastapi import HTTPException

from baseline_hub.catalog import CatalogFile
from baseline_hub.catalog_exports import preview_export
from baseline_hub.export_files import export_files
from baseline_hub.publishing import PublishingError, changes


def dump(document):
    return yaml.safe_dump(document, sort_keys=False, allow_unicode=True)


def prepare_files(github, snapshot, exported, source, http):
    name = exported.get("name", "")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,127}", name):
        raise PublishingError("Invalid exported model name")
    existing = github.model(snapshot, name, source.get("hf", ""))
    if existing:
        if existing.metadata.get("apiVersion") != "catalog.swiss/v2":
            raise PublishingError("Target model must be migrated to catalog.swiss/v2 first")
        name = existing.name
        # The remote index is not the target repository: rebuild complete files using
        # the target's canonical model name and confirmed Hugging Face identity.
        rebuilt = preview_export(
            http,
            source
            | {
                "name": name,
                "hf": existing.metadata.get("source", {}).get("hf", ""),
                "use_catalog": False,
                "default_variant": "",
            },
        ).json()
        exported.update(rebuilt)
    if not exported.get("valid"):
        raise HTTPException(422, "; ".join(exported.get("errors") or ["Catalog export is invalid"]))
    files = [CatalogFile(**file) for file in export_files(exported["files"])]
    # Validate every path before parsing or rewriting any returned content.
    changes(github, {"paths": {}}, files, name)
    if len({file.path for file in files}) != len(files):
        raise PublishingError("Export contains duplicate paths")
    versions = {}
    for file in files:
        if file.kind != "variant":
            continue
        document = yaml.safe_load(file.text)
        variant_id = document["id"]
        old = existing.variants.get(variant_id) if existing else None
        if old:
            previous = old.get("document")
            version = old["version"]
            if {k: v for k, v in (previous or {}).items() if k != "version"} != {
                k: v for k, v in document.items() if k != "version"
            }:
                match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", version)
                if not match:
                    raise PublishingError("Target variant has an invalid version")
                version = f"{match[1]}.{int(match[2]) + 1}.0"
            document["version"] = version
            file.text = dump(document)
            # Preserve original formatting for content-identical variants.
            if previous == document and file.path in snapshot["paths"]:
                file.text = github.blob(snapshot["paths"][file.path])
        versions[variant_id] = document["version"]
    for file in files:
        if file.kind == "metadata":
            metadata = yaml.safe_load(file.text)
            tuning = copy.deepcopy(metadata.get("tuning") or [])
            for item in tuning:
                for key in ("baseline", "optimized"):
                    variant_id = item.get(key, "").split("@")[0]
                    if variant_id in versions:
                        item[key] = f"{variant_id}@{versions[variant_id]}"
            if existing:
                metadata = copy.deepcopy(existing.metadata)
                if source.get("default_variant"):
                    if source["default_variant"] not in (set(existing.variants) | set(versions)):
                        raise HTTPException(
                            422, "Default variant is not present in the target model"
                        )
                    metadata["defaultVariant"] = source["default_variant"]
                elif not metadata.get("defaultVariant") and exported["metadata"].get(
                    "defaultVariant"
                ):
                    metadata["defaultVariant"] = exported["metadata"]["defaultVariant"]
                tuning = [
                    item for item in metadata.get("tuning", []) if item not in tuning
                ] + tuning
            if tuning:
                metadata["tuning"] = tuning
            file.text = dump(metadata)
            if existing and metadata == existing.metadata:
                file.text = existing.metadata_text
    return changes(github, snapshot, files, name), name, exported.get("provenance", [])
