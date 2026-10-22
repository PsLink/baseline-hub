"""Bounded, credential-scoped reads of immutable GitHub catalog content.

Mutable repository metadata, authorization and branch HEAD are never cached here.
The HTTP adapter must resolve a fresh snapshot before using these content reads.
"""

import sys
from collections import OrderedDict
from concurrent.futures import Future
from copy import deepcopy
from dataclasses import is_dataclass
from threading import Lock

import yaml

from baseline_hub.catalog import CatalogModel
from baseline_hub.publishing_errors import PublishingError


def _size(value, seen=None):
    """Account for nested Python objects, including decoded text and cache keys."""
    if seen is None:
        seen = set()
    if id(value) in seen:
        return 0
    seen.add(id(value))
    size = sys.getsizeof(value)
    if isinstance(value, dict):
        size += sum(_size(k, seen) + _size(v, seen) for k, v in value.items())
    elif isinstance(value, (tuple, list, set, frozenset)):
        size += sum(_size(item, seen) for item in value)
    elif is_dataclass(value):
        size += _size(vars(value), seen)
    return size


class CatalogCache:
    """An LRU budget shared across blobs, parsed YAML, trees and model lookups.

    Concurrent readers of the same key share one in-flight load. Failures and
    oversized values are not retained; no HTTP client or token is stored.
    """

    def __init__(self, *, max_entries=8192, max_bytes=32 * 1024 * 1024):
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self._entries = OrderedDict()
        self._pending = {}
        self._bytes = 0
        self._lock = Lock()

    def get(self, key, load):
        with self._lock:
            if key in self._entries:
                value, _ = self._entries[key]
                self._entries.move_to_end(key)
                return value
            pending = self._pending.get(key)
            owner = pending is None
            if owner:
                pending = self._pending[key] = Future()
        if not owner:
            return pending.result()
        try:
            value = load()
            size = _size((key, value))
            with self._lock:
                if self.max_entries > 0 and size <= self.max_bytes:
                    while self._entries and (
                        len(self._entries) >= self.max_entries
                        or self._bytes + size > self.max_bytes
                    ):
                        _, (_, removed) = self._entries.popitem(last=False)
                        self._bytes -= removed
                    self._entries[key] = value, size
                    self._bytes += size
            pending.set_result(value)
            return value
        except BaseException as exc:
            pending.set_exception(exc)
            raise
        finally:
            with self._lock:
                del self._pending[key]


catalog_cache = CatalogCache()


class CatalogLookup:
    def __init__(self, scope, cache):
        self.scope = scope
        self.cache = cache

    def blob(self, sha, load):
        return self.cache.get((self.scope, "blob", sha), load)

    def paths(self, tree_sha, load):
        def read():
            tree = load()
            if tree.get("truncated"):
                raise PublishingError("Catalog tree is truncated; cannot build a safe preview")
            return {item["path"]: item["sha"] for item in tree["tree"] if item["type"] == "blob"}

        return dict(self.cache.get((self.scope, "tree", tree_sha), read))

    def _document(self, sha, read_blob):
        def read():
            try:
                return yaml.safe_load(read_blob(sha))
            except yaml.YAMLError:
                raise PublishingError("Invalid catalog YAML") from None

        return self.cache.get((self.scope, "yaml", sha), read)

    def model(self, snapshot, name, hf, read_blob):
        paths = snapshot["paths"]
        identity = snapshot.get("tree") or tuple(sorted(paths.items()))
        key = self.scope, "model", identity, name, hf.lower()
        return deepcopy(self.cache.get(key, lambda: self._model(paths, name, hf, read_blob)))

    def _model(self, paths, name, hf, read_blob):
        preferred = f"models/{name}/metadata.yaml"
        if hf:
            candidates = sorted(
                path for path in paths
                if path.startswith("models/") and path.endswith("/metadata.yaml")
            )
            if preferred in candidates:
                candidates.remove(preferred)
                candidates.insert(0, preferred)
        else:
            candidates = [preferred] if preferred in paths else []
        for path in candidates:
            meta = self._document(paths[path], read_blob)
            if not isinstance(meta, dict):
                raise PublishingError("Invalid catalog metadata")
            source = meta.get("source") or {}
            if not isinstance(source, dict):
                raise PublishingError("Invalid catalog metadata")
            if hf and str(source.get("hf", "")).lower() != hf.lower():
                continue
            name = path.split("/")[1]
            model = CatalogModel(name=name, metadata=meta, metadata_text=read_blob(paths[path]))
            for variant, sha in paths.items():
                if variant.startswith(f"models/{name}/") and variant.endswith((".yaml", ".yml")):
                    if variant == path:
                        continue
                    doc = self._document(sha, read_blob)
                    if isinstance(doc, dict) and doc.get("id"):
                        model.variants[str(doc["id"])] = {
                            "version": str(doc.get("version", "")), "document": doc,
                        }
            return model
        if preferred in paths:
            raise PublishingError("Catalog model name belongs to a different Hugging Face model")
        return None
