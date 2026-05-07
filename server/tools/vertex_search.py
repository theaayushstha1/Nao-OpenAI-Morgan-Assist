# DEPRECATED — Phase 5: replaced by cs_navigator_search. Kept as fallback only.
#
# Phase 5 of the v2 rework (see ``docs/PHASE_5_TASK_MAP.md``) moves Morgan-CS
# knowledge to the operator's deployed CS Navigator Cloud Run API. This module
# previously took over from ``pinecone_search`` on commit 4deb236 and is now
# itself superseded by ``server.tools.cs_navigator.cs_navigator_search``.
#
# Why we keep the file: the Phase 5 PRD requires the previous search tool to
# stay live as a fallback for one phase + 3 stable sessions before deletion,
# so ``server/agents/chatbot.py`` falls back here when the cs_navigator
# worktree has not yet been merged. Do not import this from any other agent.
"""RAG tool backed by Google Vertex AI Search.

Queries CS Navigator's shared datastore (default `csnavigator-kb-v7`, project
`csnavigator-vertex-ai`). Authentication uses Application Default Credentials:
either `gcloud auth application-default login` for local dev, or
`GOOGLE_APPLICATION_CREDENTIALS` pointing at a service account JSON in prod.
"""
from __future__ import annotations

from agents import function_tool
from server import config

try:
    from google.api_core.client_options import ClientOptions
    from google.cloud import discoveryengine_v1 as discoveryengine

    # Vertex AI Search uses regional endpoints for non-global datastores.
    _api_endpoint = (
        "discoveryengine.googleapis.com"
        if config.VERTEX_LOCATION == "global"
        else f"{config.VERTEX_LOCATION}-discoveryengine.googleapis.com"
    )
    _client = discoveryengine.SearchServiceClient(
        client_options=ClientOptions(api_endpoint=_api_endpoint)
    )
    _serving_config = _client.serving_config_path(
        project=config.GOOGLE_CLOUD_PROJECT,
        location=config.VERTEX_LOCATION,
        data_store=config.VERTEX_DATASTORE_ID,
        serving_config="default_config",
    )
except Exception as e:  # noqa: BLE001 — tool stays callable, returns empty on failure
    _client = None
    _serving_config = None
    _init_error = repr(e)
else:
    _init_error = None


def _snippet_of(doc) -> str:
    """Best-effort extract a text chunk from a Vertex AI Search document."""
    dsd = doc.derived_struct_data or {}
    if "snippets" in dsd:
        for s in dsd["snippets"]:
            if s.get("snippet"):
                return s["snippet"]
    struct = doc.struct_data or {}
    for key in ("text", "content", "body", "chunk"):
        if key in struct and struct[key]:
            return str(struct[key])
    return ""


def _search_impl(query: str, top_k: int = 5) -> list[dict]:
    if _client is None or _serving_config is None:
        return []
    req = discoveryengine.SearchRequest(
        serving_config=_serving_config,
        query=query,
        page_size=top_k,
        content_search_spec=discoveryengine.SearchRequest.ContentSearchSpec(
            snippet_spec=discoveryengine.SearchRequest.ContentSearchSpec.SnippetSpec(
                return_snippet=True,
            ),
        ),
    )
    try:
        resp = _client.search(req)
    except Exception:  # noqa: BLE001 — fail soft; chatbot agent will say "not sure"
        return []
    out = []
    for result in resp.results:
        text = _snippet_of(result.document)
        if not text:
            continue
        out.append({"text": text, "score": 1.0})  # Vertex doesn't expose per-doc scores via this API
        if len(out) >= top_k:
            break
    return out


@function_tool
def vertex_search(query: str, top_k: int = 5) -> list[dict]:
    """Search the Morgan State CS knowledge base. Returns top_k passages with text snippets."""
    return _search_impl(query, top_k)
