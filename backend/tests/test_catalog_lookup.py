"""Catalog reads reuse immutable content without freezing authorization or branch HEAD."""

import base64
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from time import sleep

import httpx
import pytest

from baseline_hub import publishing as pub
from baseline_hub.catalog_lookup import CatalogCache


@pytest.fixture
def catalog():
    state = {
        "head": "commit-1", "tree": "tree-1", "authorized": True,
        "paths": {
            "models/alpha/metadata.yaml": "alpha-meta",
            "models/demo/metadata.yaml": "demo-meta",
            "models/demo/base.yaml": "base-1",
        },
        "blobs": {
            "alpha-meta": "source: {hf: Org/Alpha}\n",
            "demo-meta": "source: {hf: Org/Demo}\n",
            "base-1": "id: base\nversion: 1.0.0\n",
            "base-2": "id: base\nversion: 1.1.0\n",
        },
    }
    calls = []
    clients = []
    shared_cache = CatalogCache()

    def remote(request):
        calls.append(request)
        if not state["authorized"]:
            return httpx.Response(401)
        path = request.url.path
        if "/git/blobs/" in path:
            sha = path.rsplit("/", 1)[1]
            if state.get("failed_blob") == sha:
                return httpx.Response(429, headers={"retry-after": "30"})
            if state.get("slow_blob") == sha:
                sleep(0.03)
            return httpx.Response(200, json={
                "content": base64.b64encode(state["blobs"][sha].encode()).decode(),
            })
        if "/git/trees/" in path:
            return httpx.Response(200, json={"tree": [
                {"type": "blob", "path": path, "sha": sha}
                for path, sha in state["paths"].items()
            ], "truncated": state.get("truncated", False)})
        if "/commits/" in path:
            return httpx.Response(200, json={
                "sha": state["head"], "commit": {"tree": {"sha": state["tree"]}},
            })
        return httpx.Response(200, json={"id": 1, "default_branch": "main"})

    def github(token="lookup-fixture", repository="team/catalog", *, cache=None):
        client = pub.Github(
            token, repository=repository, cache=shared_cache if cache is None else cache
        )
        client.client.close()
        client.client = httpx.Client(
            base_url="https://api.github.com", transport=httpx.MockTransport(remote)
        )
        clients.append(client)
        return client

    yield github, state, calls
    for client in clients:
        client.close()


def counts(calls):
    return Counter(request.url.path for request in calls)


def test_repeated_previews_reuse_tree_and_blobs_across_github_clients(catalog):
    github, _, calls = catalog
    first = github("reuse-token")
    first.model(first.snapshot(), "new-name", "Org/Demo")
    second = github("reuse-token")
    model = second.model(second.snapshot(), "new-name", "Org/Demo")
    assert model.name == "demo"
    requests = counts(calls)
    assert requests["/repos/team/catalog"] == 2
    assert requests["/repos/team/catalog/commits/main"] == 2
    assert requests["/repos/team/catalog/git/trees/tree-1"] == 1
    assert requests["/repos/team/catalog/git/blobs/alpha-meta"] == 1
    assert requests["/repos/team/catalog/git/blobs/demo-meta"] == 1
    assert requests["/repos/team/catalog/git/blobs/base-1"] == 1


def test_new_commit_reuses_unchanged_blobs_but_reads_the_new_variant(catalog):
    github, state, calls = catalog
    first = github("updated-commit")
    assert first.model(first.snapshot(), "demo", "Org/Demo").variants["base"]["version"] == "1.0.0"
    state["head"], state["tree"] = "commit-2", "tree-2"
    state["paths"]["models/demo/base.yaml"] = "base-2"
    second = github("updated-commit")
    snapshot = second.snapshot()
    assert snapshot["sha"] == "commit-2"
    assert second.model(snapshot, "demo", "Org/Demo").variants["base"]["version"] == "1.1.0"
    assert counts(calls)["/repos/team/catalog/git/blobs/demo-meta"] == 1
    assert counts(calls)["/repos/team/catalog/git/blobs/base-2"] == 1


def test_a_cached_lookup_miss_does_not_hide_a_model_added_in_a_new_tree(catalog):
    github, state, _ = catalog
    first = github("new-model")
    assert first.model(first.snapshot(), "future", "Org/Future") is None
    state["head"], state["tree"] = "commit-2", "tree-2"
    state["paths"]["models/future/metadata.yaml"] = "future-meta"
    state["blobs"]["future-meta"] = "source: {hf: Org/Future}\n"
    second = github("new-model")
    assert second.model(second.snapshot(), "future", "Org/Future").name == "future"


def test_cached_content_never_skips_fresh_authorization(catalog):
    github, state, _ = catalog
    first = github("revoked-token")
    first.model(first.snapshot(), "demo", "Org/Demo")
    state["authorized"] = False
    with pytest.raises(pub.PublishingError) as error:
        github("revoked-token").snapshot()
    assert error.value.status == "needs_authorization"


@pytest.mark.parametrize("different", ["credential", "repository"])
def test_cached_content_is_isolated_by_repository_and_credential(catalog, different):
    github, _, calls = catalog
    first = github("scoped-token")
    first.model(first.snapshot(), "demo", "Org/Demo")
    second = github(
        "another-token" if different == "credential" else "scoped-token",
        "other/catalog" if different == "repository" else "team/catalog",
    )
    before = len(calls)
    second.model(second.snapshot(), "demo", "Org/Demo")
    assert any("/git/blobs/" in request.url.path for request in calls[before:])


def test_returned_models_and_paths_cannot_poison_a_cached_snapshot(catalog):
    github, _, calls = catalog
    first = github("mutable-results")
    snapshot = first.snapshot()
    model = first.model(snapshot, "demo", "Org/Demo")
    model.metadata["source"]["hf"] = "Changed/Identity"
    model.variants["base"]["document"]["version"] = "99.0.0"
    snapshot["paths"].clear()
    second = github("mutable-results")
    fresh = second.snapshot()
    result = second.model(fresh, "demo", "Org/Demo")
    assert result.metadata["source"]["hf"] == "Org/Demo"
    assert result.variants["base"]["document"]["version"] == "1.0.0"
    assert counts(calls)["/repos/team/catalog/git/trees/tree-1"] == 1


def test_an_exact_name_lookup_does_not_scan_unrelated_models(catalog):
    github, _, calls = catalog
    client = github("name-only")
    assert client.model(client.snapshot(), "missing", "") is None
    assert not any("/git/blobs/" in request.url.path for request in calls)


def test_truncated_trees_are_not_cached(catalog):
    github, state, calls = catalog
    state["truncated"] = True
    with pytest.raises(pub.PublishingError):
        github("truncated-tree").snapshot()
    state["truncated"] = False
    assert github("truncated-tree").snapshot()["paths"]
    assert counts(calls)["/repos/team/catalog/git/trees/tree-1"] == 2


def test_branch_head_is_fresh_even_when_the_new_commit_has_the_same_tree(catalog):
    github, state, calls = catalog
    assert github().snapshot()["sha"] == "commit-1"
    state["head"] = "empty-commit-2"
    assert github().snapshot()["sha"] == "empty-commit-2"
    assert counts(calls)["/repos/team/catalog/git/trees/tree-1"] == 1


def test_unchanged_yaml_is_not_parsed_again_when_a_variant_changes(catalog, monkeypatch):
    from baseline_hub import catalog_lookup

    github, state, _ = catalog
    parsed = []
    original = catalog_lookup.yaml.safe_load

    def parse(text):
        parsed.append(text)
        return original(text)

    monkeypatch.setattr(catalog_lookup.yaml, "safe_load", parse)
    first = github()
    first.model(first.snapshot(), "demo", "Org/Demo")
    state["head"], state["tree"] = "commit-2", "tree-2"
    state["paths"]["models/demo/base.yaml"] = "base-2"
    second = github()
    second.model(second.snapshot(), "demo", "Org/Demo")
    assert parsed.count(state["blobs"]["demo-meta"]) == 1
    assert parsed.count(state["blobs"]["base-1"]) == 1
    assert parsed.count(state["blobs"]["base-2"]) == 1


def test_failed_blob_reads_are_not_cached(catalog):
    github, state, calls = catalog
    state["failed_blob"] = "demo-meta"
    first = github()
    with pytest.raises(pub.PublishingError) as error:
        first.model(first.snapshot(), "demo", "Org/Demo")
    assert error.value.status == "queued"
    state["failed_blob"] = None
    second = github()
    assert second.model(second.snapshot(), "demo", "Org/Demo").name == "demo"
    assert counts(calls)["/repos/team/catalog/git/blobs/demo-meta"] == 2


def test_parallel_readers_of_the_same_blob_share_one_request(catalog):
    github, state, calls = catalog
    state["slow_blob"] = "demo-meta"
    readers = [github() for _ in range(8)]
    barrier = Barrier(len(readers))

    def read(client):
        barrier.wait(timeout=5)
        return client.blob("demo-meta")

    with ThreadPoolExecutor(max_workers=len(readers)) as pool:
        assert list(pool.map(read, readers)) == [state["blobs"]["demo-meta"]] * len(readers)
    assert counts(calls)["/repos/team/catalog/git/blobs/demo-meta"] == 1


def test_cache_entry_limit_evicts_old_content(catalog):
    github, _, calls = catalog
    cache = CatalogCache(max_entries=1, max_bytes=4096)
    client = github(cache=cache)
    client.blob("demo-meta")
    client.blob("alpha-meta")
    client.blob("demo-meta")
    assert counts(calls)["/repos/team/catalog/git/blobs/demo-meta"] == 2


def test_oversized_content_is_returned_but_not_retained(catalog):
    github, state, calls = catalog
    state["blobs"]["large"] = "x" * 10000
    client = github(cache=CatalogCache(max_bytes=1024))
    assert client.blob("large") == state["blobs"]["large"]
    assert client.blob("large") == state["blobs"]["large"]
    assert counts(calls)["/repos/team/catalog/git/blobs/large"] == 2
