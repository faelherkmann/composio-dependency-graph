from __future__ import annotations

import html
import json
import os
import re
import textwrap
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


API_BASE = "https://backend.composio.dev/api/v3.1"
TOOLKITS = ("googlesuper", "github")
PAGE_SIZE = 200
PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_JSON = PROJECT_ROOT / "dependency_graph.json"
OUTPUT_HTML = PROJECT_ROOT / "dependency_graph.html"


DIRECT_USER_INPUT_FIELDS = {
    "title",
    "body",
    "text",
    "content",
    "description",
    "summary",
    "message",
    "subject",
    "note",
    "notes",
    "query",
    "url",
    "path",
    "filename",
    "file_name",
    "workspace",
    "language",
    "state",
    "status",
    "sort",
    "order",
    "page",
    "limit",
    "cursor",
    "fields",
    "labels",
    "assignees",
}

CONTROL_FIELDS = {
    "page",
    "limit",
    "cursor",
    "offset",
    "sort",
    "order",
    "include",
    "exclude",
    "filter",
    "fields",
    "query",
}

TOOLKIT_STYLES = {
    "googlesuper": {"color": "#F97316", "border": "#C2410C"},
    "github": {"color": "#06B6D4", "border": "#0F766E"},
    "entity": {"color": "#E5E7EB", "border": "#94A3B8"},
    "user": {"color": "#111827", "border": "#374151"},
}


def load_env() -> None:
    env_file = PROJECT_ROOT / ".env"
    if env_file.exists():
        for raw_line in env_file.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def api_key() -> str:
    load_env()
    key = os.environ.get("COMPOSIO_API_KEY", "").strip()
    if not key:
        raise RuntimeError("COMPOSIO_API_KEY is required to fetch Composio metadata")
    return key


def fetch_json(path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
    url = f"{API_BASE}{path}"
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(
        url,
        headers={
            "x-api-key": api_key(),
            "accept": "application/json, text/plain, */*",
            "accept-language": "en-US,en;q=0.9",
            "cache-control": "no-cache",
            "pragma": "no-cache",
            "referer": "https://platform.composio.dev/",
            "user-agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "cross-site",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        message = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"Composio request failed for {url}: {exc.code} {message}") from exc


def slug_to_label(slug: str) -> str:
    pieces = [piece for piece in re.split(r"[^a-z0-9]+", slug.lower()) if piece]
    if not pieces:
        return slug.lower()
    if pieces[0] in {"github", "googlesuper"}:
        pieces = pieces[1:]
    return " ".join(pieces)


def canonicalize_phrase(text: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()
    normalized = re.sub(r"\s+", " ", normalized)

    replacements = [
        (r"pull request", "pull_request"),
        (r"pull request number", "pull_request_number"),
        (r"pull request id", "pull_request_number"),
        (r"repository", "repo"),
        (r"repository name", "repo"),
        (r"repo name", "repo"),
        (r"email address", "email"),
        (r"recipient email", "email"),
        (r"sender email", "email"),
        (r"thread id", "thread_id"),
        (r"message id", "message_id"),
        (r"calendar id", "calendar_id"),
        (r"event id", "event_id"),
        (r"contact id", "contact_id"),
        (r"issue number", "issue_number"),
        (r"issue id", "issue_number"),
        (r"commit sha", "commit_sha"),
        (r"commit id", "commit_sha"),
        (r"file id", "file_id"),
        (r"folder id", "folder_id"),
        (r"sheet id", "sheet_id"),
        (r"document id", "document_id"),
        (r"presentation id", "presentation_id"),
        (r"project id", "project_id"),
        (r"workspace id", "workspace_id"),
        (r"user id", "user_id"),
        (r"team id", "team_id"),
        (r"acl rule id", "acl_rule_id"),
        (r"rule id", "acl_rule_id"),
        (r"permission id", "permission_id"),
        (r"comment id", "comment_id"),
        (r"review id", "review_id"),
        (r"webhook id", "webhook_id"),
    ]
    for pattern, replacement in replacements:
        normalized = re.sub(pattern, replacement, normalized)

    return normalized.replace(" ", "_")


def infer_tool_context(tool: dict[str, Any]) -> list[str]:
    text = f"{tool.get('slug', '')} {tool.get('name', '')} {tool.get('description', '')}".lower()
    matches: list[str] = []

    ordered_hints = [
        ("pull request", "pull_request"),
        ("repository", "repo"),
        ("repo", "repo"),
        ("issue", "issue"),
        ("commit", "commit"),
        ("branch", "branch"),
        ("review", "review"),
        ("comment", "comment"),
        ("workflow", "workflow"),
        ("check run", "check_run"),
        ("release", "release"),
        ("tag", "tag"),
        ("team", "team"),
        ("organization", "organization"),
        ("project", "project"),
        ("file", "file"),
        ("folder", "folder"),
        ("drive", "drive"),
        ("document", "document"),
        ("spreadsheet", "sheet"),
        ("sheet", "sheet"),
        ("presentation", "presentation"),
        ("calendar", "calendar"),
        ("event", "event"),
        ("thread", "thread"),
        ("message", "message"),
        ("email", "email"),
        ("contact", "contact"),
        ("label", "label"),
        ("user", "user"),
        ("member", "user"),
        ("acl", "acl_rule"),
        ("permission", "permission"),
        ("webhook", "webhook"),
    ]

    for needle, label in ordered_hints:
        if needle in text and label not in matches:
            matches.append(label)

    return matches


def resolve_ref(schema: dict[str, Any], ref: str) -> dict[str, Any] | None:
    if not ref.startswith("#/"):
        return None
    current: Any = schema
    for part in ref[2:].split("/"):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return current if isinstance(current, dict) else None


def coerce_required(required: Any) -> set[str]:
    if not required:
        return set()
    if isinstance(required, list):
        return {str(item) for item in required}
    if isinstance(required, str):
        return {item for item in re.split(r"[\s,]+", required) if item}
    return {str(required)}


@dataclass
class FieldInfo:
    path: str
    name: str
    display_name: str
    description: str
    type: str
    required: bool
    canonical: str | None
    confidence: float = 0.0


@dataclass
class ToolInfo:
    slug: str
    toolkit: str
    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    tags: list[str] = field(default_factory=list)
    resource_hints: list[str] = field(default_factory=list)
    required_fields: list[FieldInfo] = field(default_factory=list)
    output_fields: list[FieldInfo] = field(default_factory=list)


def infer_tool_context_from_text(text: str) -> list[str]:
    text = text.lower()
    contexts: list[str] = []
    ordered = [
        ("pull request", "pull_request"),
        ("repository", "repo"),
        ("repo", "repo"),
        ("issue", "issue"),
        ("commit", "commit"),
        ("branch", "branch"),
        ("review", "review"),
        ("comment", "comment"),
        ("workflow", "workflow"),
        ("check run", "check_run"),
        ("release", "release"),
        ("tag", "tag"),
        ("team", "team"),
        ("organization", "organization"),
        ("project", "project"),
        ("file", "file"),
        ("folder", "folder"),
        ("drive", "drive"),
        ("document", "document"),
        ("spreadsheet", "sheet"),
        ("sheet", "sheet"),
        ("presentation", "presentation"),
        ("calendar", "calendar"),
        ("event", "event"),
        ("thread", "thread"),
        ("message", "message"),
        ("email", "email"),
        ("contact", "contact"),
        ("label", "label"),
        ("user", "user"),
        ("member", "user"),
        ("acl", "acl_rule"),
        ("permission", "permission"),
        ("webhook", "webhook"),
        ("user id", "user_id"),
    ]
    for needle, label in ordered:
        if needle in text and label not in contexts:
            contexts.append(label)
    return contexts


def infer_entity_name(name: str, display_name: str, description: str, context_text: str) -> str | None:
    normalized_name = name.lower().strip()
    normalized_context = context_text.lower()

    if normalized_name in CONTROL_FIELDS:
        return None

    if normalized_name in DIRECT_USER_INPUT_FIELDS:
        return None

    direct_name_aliases = {
        "repo": "repo",
        "repository": "repo",
        "repository_id": "repo",
        "repository_name": "repo",
        "owner": "owner",
        "owner_login": "owner",
        "thread_id": "thread_id",
        "message_id": "message_id",
        "email": "email",
        "email_address": "email",
        "contact_id": "contact_id",
        "calendar_id": "calendar_id",
        "event_id": "event_id",
        "issue_number": "issue_number",
        "issue_id": "issue_number",
        "pull_request_number": "pull_request_number",
        "pull_request_id": "pull_request_number",
        "pr_number": "pull_request_number",
        "commit_sha": "commit_sha",
        "sha": "commit_sha",
        "branch": "branch",
        "branch_name": "branch",
        "label": "label",
        "label_name": "label",
        "file_id": "file_id",
        "folder_id": "folder_id",
        "sheet_id": "sheet_id",
        "spreadsheet_id": "sheet_id",
        "document_id": "document_id",
        "presentation_id": "presentation_id",
        "project_id": "project_id",
        "workspace_id": "workspace_id",
        "user_id": "user_id",
        "team_id": "team_id",
        "acl_rule_id": "acl_rule_id",
        "rule_id": "acl_rule_id",
        "permission_id": "permission_id",
        "comment_id": "comment_id",
        "review_id": "review_id",
        "webhook_id": "webhook_id",
    }
    if normalized_name in direct_name_aliases:
        return direct_name_aliases[normalized_name]

    text = f"{name} {display_name} {description}".lower()
    if re.search(r"\bemail(address)?\b", text):
        return "email"
    if re.search(r"\bthread[_ ]?id\b|\bthread\b", text):
        return "thread_id"
    if re.search(r"\bmessage[_ ]?id\b|\bmessage\b", text):
        return "message_id"
    if re.search(r"\bcalendar[_ ]?id\b|\bcalendar\b", text):
        return "calendar_id"
    if re.search(r"\bevent[_ ]?id\b|\bevent\b", text):
        return "event_id"
    if re.search(r"\bcontact[_ ]?id\b|\bcontact\b", text):
        return "contact_id"
    if re.search(r"\bissue[_ ]?(number|id)\b|\bissue\b", text):
        return "issue_number"
    if re.search(r"\bpull[_ ]?request[_ ]?(number|id)\b|\bpr[_ ]?number\b|\bpull request\b", text):
        return "pull_request_number"
    if re.search(r"\bcommit[_ ]?(sha|id)\b|\bsha\b|\bcommit\b", text):
        return "commit_sha"
    if re.search(r"\bfile[_ ]?id\b|\bfile\b", text):
        return "file_id"
    if re.search(r"\bfolder[_ ]?id\b|\bfolder\b", text):
        return "folder_id"
    if re.search(r"\bsheet[_ ]?id\b|\bsheet\b|\bspreadsheet\b", text):
        return "sheet_id"
    if re.search(r"\bdocument[_ ]?id\b|\bdocument\b|\bdoc\b", text):
        return "document_id"
    if re.search(r"\bpresentation[_ ]?id\b|\bpresentation\b|\bslides\b", text):
        return "presentation_id"
    if re.search(r"\bproject[_ ]?id\b|\bproject\b", text):
        return "project_id"
    if re.search(r"\bworkspace[_ ]?id\b|\bworkspace\b", text):
        return "workspace_id"
    if re.search(r"\buser[_ ]?id\b|\bmember[_ ]?id\b|\buser\b", text):
        return "user_id"
    if re.search(r"\bteam[_ ]?id\b|\bteam\b", text):
        return "team_id"
    if re.search(r"\bacl[_ ]?rule[_ ]?id\b|\brule[_ ]?id\b|\bacl\b", text):
        return "acl_rule_id"
    if re.search(r"\bpermission[_ ]?id\b|\bpermission\b", text):
        return "permission_id"
    if re.search(r"\bcomment[_ ]?id\b|\bcomment\b", text):
        return "comment_id"
    if re.search(r"\breview[_ ]?id\b|\breview\b", text):
        return "review_id"
    if re.search(r"\bwebhook[_ ]?id\b|\bwebhook\b", text):
        return "webhook_id"

    suffix_match = re.match(r"^(?P<base>[a-z0-9_ ]+?)[_ ](?P<suffix>id|number|name|login|slug|path|sha|url)$", normalized_name)
    if suffix_match:
        base = canonicalize_phrase(suffix_match.group("base"))
        suffix = suffix_match.group("suffix")
        if base:
            return f"{base}_{suffix}"

    if normalized_name in {"id", "number", "name", "login", "slug", "path", "sha"}:
        contexts = infer_tool_context_from_text(normalized_context)
        if contexts:
            return f"{contexts[0]}_{normalized_name}"

    if any(token in normalized_context for token in ["email", "thread", "message", "repo", "issue", "pull request", "calendar", "event", "contact", "commit", "branch", "label", "document", "sheet", "presentation", "project", "workspace"]):
        contexts = infer_tool_context_from_text(normalized_context)
        if contexts and normalized_name in {"id", "number", "name", "login", "slug", "sha"}:
            return f"{contexts[0]}_{normalized_name}"

    return None


def field_confidence(name: str, description: str, canonical: str | None) -> float:
    if not canonical:
        return 0.0
    score = 0.35
    lower = f"{name} {description}".lower()
    if name.lower() == canonical:
        score += 0.4
    if any(token in lower for token in ["id", "number", "email", "login", "sha", "thread", "issue", "repo", "pull request", "calendar", "event", "contact", "file", "folder"]):
        score += 0.2
    if description:
        score += 0.05
    return min(score, 1.0)


def walk_schema(
    schema: dict[str, Any],
    *,
    path: str = "",
    required_fields: set[str] | None = None,
    ancestors: list[str] | None = None,
    root_contexts: list[str] | None = None,
) -> Iterable[FieldInfo]:
    required_fields = required_fields or set()
    ancestors = ancestors or []
    root_contexts = root_contexts or []

    if "$ref" in schema:
        resolved = resolve_ref(schema, str(schema["$ref"]))
        if resolved:
            yield from walk_schema(
                resolved,
                path=path,
                required_fields=required_fields,
                ancestors=ancestors,
                root_contexts=root_contexts,
            )
        return

    if "allOf" in schema:
        for item in schema.get("allOf", []):
            if isinstance(item, dict):
                yield from walk_schema(
                    item,
                    path=path,
                    required_fields=required_fields,
                    ancestors=ancestors,
                    root_contexts=root_contexts,
                )

    properties = schema.get("properties")
    if isinstance(properties, dict):
        local_required = coerce_required(schema.get("required"))

        for prop_name, prop_schema in properties.items():
            if not isinstance(prop_schema, dict):
                continue

            field_path = f"{path}.{prop_name}" if path else prop_name
            display_name = prop_schema.get("title") or prop_name
            field_type = str(prop_schema.get("type") or prop_schema.get("format") or "unknown")
            description = str(prop_schema.get("description") or "")
            is_required = prop_name in local_required or prop_name in required_fields
            context_text = " ".join(root_contexts + ancestors + [prop_name, display_name, description])
            canonical = infer_entity_name(prop_name, display_name, description, context_text)

            yield FieldInfo(
                path=field_path,
                name=prop_name,
                display_name=display_name,
                description=description,
                type=field_type,
                required=is_required,
                canonical=canonical,
                confidence=field_confidence(prop_name, description, canonical),
            )

            if prop_schema.get("type") == "object" or "properties" in prop_schema:
                next_context = root_contexts + [canonical or prop_name]
                yield from walk_schema(
                    prop_schema,
                    path=field_path,
                    required_fields=coerce_required(prop_schema.get("required")),
                    ancestors=ancestors + [prop_name],
                    root_contexts=next_context,
                )

            if prop_schema.get("type") == "array" and isinstance(prop_schema.get("items"), dict):
                items_schema = prop_schema["items"]
                yield from walk_schema(
                    items_schema,
                    path=f"{field_path}[*]",
                    required_fields=coerce_required(items_schema.get("required")),
                    ancestors=ancestors + [prop_name],
                    root_contexts=root_contexts + [canonical or prop_name],
                )

    if "items" in schema and isinstance(schema["items"], dict):
        yield from walk_schema(
            schema["items"],
            path=f"{path}[*]" if path else "[*]",
            required_fields=coerce_required(schema["items"].get("required")),
            ancestors=ancestors,
            root_contexts=root_contexts,
        )


def load_tools() -> list[ToolInfo]:
    tools: list[ToolInfo] = []
    for toolkit in TOOLKITS:
        cursor: str | None = None
        seen: set[str] = set()
        while True:
            params = {"toolkit_slug": toolkit, "limit": str(PAGE_SIZE)}
            if cursor:
                params["cursor"] = cursor
            payload = fetch_json("/tools", params)
            items = payload.get("items", [])
            for item in items:
                slug = str(item.get("slug") or "")
                if not slug or slug in seen:
                    continue
                seen.add(slug)
                input_schema = item.get("input_parameters") or {}
                output_schema = item.get("output_parameters") or {}
                tools.append(
                    ToolInfo(
                        slug=slug,
                        toolkit=toolkit,
                        name=str(item.get("name") or slug_to_label(slug)),
                        description=str(item.get("description") or ""),
                        input_schema=input_schema if isinstance(input_schema, dict) else {},
                        output_schema=output_schema if isinstance(output_schema, dict) else {},
                        tags=[str(tag) for tag in item.get("tags") or []],
                        resource_hints=infer_tool_context(item),
                    )
                )

            cursor = payload.get("next_cursor")
            if not cursor:
                break

    return tools


def extract_schema_fields(schema: dict[str, Any], tool: ToolInfo, *, kind: str) -> list[FieldInfo]:
    root_required = coerce_required(schema.get("required"))
    contexts = infer_tool_context({"slug": tool.slug, "name": tool.name, "description": tool.description})
    fields: list[FieldInfo] = []

    for field in walk_schema(
        schema,
        required_fields=root_required,
        ancestors=[],
        root_contexts=contexts,
    ):
        if not field.canonical:
            continue
        if kind == "input" and not field.required:
            continue
        if field.canonical == "search_query":
            continue
        fields.append(field)

    return fields


def is_dependency_candidate(field: FieldInfo) -> bool:
    canonical = field.canonical or ""
    if not canonical or canonical == "search_query":
        return False

    return canonical in {
        "email",
        "contact_id",
        "thread_id",
        "message_id",
        "calendar_id",
        "event_id",
        "repo",
        "owner",
        "issue_number",
        "pull_request_number",
        "commit_sha",
        "branch",
        "label",
        "file_id",
        "folder_id",
        "sheet_id",
        "document_id",
        "presentation_id",
        "project_id",
        "workspace_id",
        "user_id",
        "team_id",
        "acl_rule_id",
        "permission_id",
        "comment_id",
        "review_id",
        "webhook_id",
    }


def describe_field(field: FieldInfo) -> str:
    description = field.description.strip()
    if description:
        return description
    return f"{field.display_name} ({field.path})"


def make_tool_node_id(tool: ToolInfo) -> str:
    return f"tool:{tool.toolkit}:{tool.slug}"


def make_entity_node_id(entity: str) -> str:
    return f"entity:{entity}"


def make_user_node_id() -> str:
    return "user:input"


def build_graph(tools: list[ToolInfo]) -> dict[str, Any]:
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    entity_producers: dict[str, list[tuple[ToolInfo, FieldInfo]]] = defaultdict(list)
    entity_consumers: dict[str, list[tuple[ToolInfo, FieldInfo]]] = defaultdict(list)
    unresolved_user_inputs: Counter[str] = Counter()

    for tool in tools:
        tool.required_fields = extract_schema_fields(tool.input_schema, tool, kind="input")
        tool.output_fields = extract_schema_fields(tool.output_schema, tool, kind="output")

    for tool in tools:
        tool_id = make_tool_node_id(tool)
        nodes[tool_id] = {
            "id": tool_id,
            "type": "tool",
            "label": tool.name,
            "title": f"{tool.slug}\n{tool.description}",
            "toolkit": tool.toolkit,
            "slug": tool.slug,
            "description": tool.description,
            "shape": "box",
            "group": tool.toolkit,
        }

        output_entities: set[str] = set()
        output_entities.update(tool.resource_hints)
        for field in tool.output_fields:
            output_entities.add(field.canonical or field.name)

        for entity in sorted(output_entities):
            entity_producers[entity].append((tool, FieldInfo("", entity, entity, tool.description, "object", False, entity)))

        for field in tool.required_fields:
            if not is_dependency_candidate(field):
                continue
            entity = field.canonical or field.name
            entity_consumers[entity].append((tool, field))

    all_entities = sorted(set(entity_producers) | set(entity_consumers))
    user_node_id = make_user_node_id()
    nodes[user_node_id] = {
        "id": user_node_id,
        "type": "user",
        "label": "user input",
        "title": "Fallback when no upstream tool can provide the required value",
        "shape": "diamond",
        "group": "user",
    }

    for entity in all_entities:
        entity_id = make_entity_node_id(entity)
        producers = entity_producers.get(entity, [])
        consumers = entity_consumers.get(entity, [])

        if entity_id not in nodes:
            nodes[entity_id] = {
                "id": entity_id,
                "type": "entity",
                "label": entity,
                "title": f"Canonical dependency entity: {entity}",
                "shape": "ellipse",
                "group": "entity",
            }

        if not producers:
            for _, field in consumers:
                unresolved_user_inputs[field.canonical or field.name] += 1
            edges.append(
                {
                    "from": user_node_id,
                    "to": entity_id,
                    "label": "provide",
                    "arrows": "to",
                    "dashes": True,
                    "color": {"color": "#64748B", "highlight": "#334155"},
                    "title": f"No tool found that reliably produces {entity}",
                }
            )
        else:
            for tool, _ in producers[:4]:
                edges.append(
                    {
                        "from": make_tool_node_id(tool),
                        "to": entity_id,
                        "label": "produces",
                        "arrows": "to",
                        "color": {"color": "#94A3B8", "highlight": "#64748B"},
                        "title": f"{tool.name} can produce {entity}",
                    }
                )

        for tool, field in consumers:
            edges.append(
                {
                    "from": entity_id,
                    "to": make_tool_node_id(tool),
                    "label": field.name,
                    "arrows": "to",
                    "color": {"color": "#F59E0B", "highlight": "#D97706"},
                    "title": f"{tool.name} requires {field.name}: {describe_field(field)}",
                }
            )

    return {
        "nodes": list(nodes.values()),
        "edges": edges,
        "stats": {
            "tools": len(tools),
            "entities": len(all_entities),
            "edges": len(edges),
            "toolkits": {toolkit: sum(1 for tool in tools if tool.toolkit == toolkit) for toolkit in TOOLKITS},
            "unresolved_user_inputs": unresolved_user_inputs.most_common(20),
        },
    }


def render_html(graph: dict[str, Any]) -> str:
    graph_json = json.dumps(graph, indent=2)
    stats = graph["stats"]
    unresolved = stats["unresolved_user_inputs"]
    unresolved_markup = "".join(
        f"<li><span>{html.escape(str(name))}</span><strong>{count}</strong></li>"
        for name, count in unresolved[:12]
    ) or "<li><span>No unresolved user-only inputs found.</span><strong>0</strong></li>"

    return textwrap.dedent(
        f"""\
        <!DOCTYPE html>
        <html lang="en">
        <head>
          <meta charset="UTF-8" />
          <meta name="viewport" content="width=device-width, initial-scale=1.0" />
          <title>Composio Dependency Graph</title>
          <script src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
          <style>
            :root {{
              color-scheme: dark;
              --bg: #0b1020;
              --panel: rgba(15, 23, 42, 0.92);
              --panel-border: rgba(148, 163, 184, 0.2);
              --text: #e5e7eb;
              --muted: #94a3b8;
            }}
            * {{ box-sizing: border-box; }}
            body {{
              margin: 0;
              min-height: 100vh;
              font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
              color: var(--text);
              background:
                radial-gradient(circle at top left, rgba(56, 189, 248, 0.18), transparent 32%),
                radial-gradient(circle at top right, rgba(251, 113, 133, 0.12), transparent 28%),
                linear-gradient(180deg, #0b1020 0%, #090d18 100%);
            }}
            header {{ padding: 28px 32px 12px; }}
            h1 {{
              margin: 0;
              font-size: clamp(28px, 4vw, 44px);
              letter-spacing: -0.04em;
            }}
            .subtitle {{
              margin-top: 10px;
              max-width: 920px;
              color: var(--muted);
              line-height: 1.55;
            }}
            main {{
              display: grid;
              grid-template-columns: minmax(320px, 420px) 1fr;
              gap: 18px;
              padding: 18px 24px 28px;
            }}
            .panel {{
              background: var(--panel);
              border: 1px solid var(--panel-border);
              border-radius: 20px;
              box-shadow: 0 24px 80px rgba(2, 6, 23, 0.4);
              backdrop-filter: blur(14px);
            }}
            .summary {{ padding: 20px; }}
            .summary-grid {{
              display: grid;
              grid-template-columns: repeat(2, minmax(0, 1fr));
              gap: 12px;
              margin: 18px 0;
            }}
            .card {{
              padding: 14px 16px;
              border-radius: 16px;
              background: rgba(15, 23, 42, 0.75);
              border: 1px solid rgba(148, 163, 184, 0.16);
            }}
            .card .label {{
              color: var(--muted);
              font-size: 12px;
              text-transform: uppercase;
              letter-spacing: 0.08em;
            }}
            .card .value {{ margin-top: 8px; font-size: 28px; font-weight: 700; }}
            .section {{ margin-top: 18px; }}
            .section h2 {{
              margin: 0 0 10px;
              font-size: 15px;
              color: #f8fafc;
              letter-spacing: 0.02em;
            }}
            .legend {{ display: grid; gap: 10px; }}
            .legend-item {{
              display: flex;
              align-items: center;
              justify-content: space-between;
              gap: 10px;
              padding: 10px 12px;
              border-radius: 14px;
              background: rgba(30, 41, 59, 0.72);
              border: 1px solid rgba(148, 163, 184, 0.14);
              color: var(--text);
            }}
            .swatch {{
              width: 14px;
              height: 14px;
              border-radius: 999px;
              flex: 0 0 auto;
            }}
            .legend-item .left {{ display: flex; align-items: center; gap: 10px; }}
            .inputs {{
              margin: 0;
              padding: 0;
              list-style: none;
              display: grid;
              gap: 8px;
            }}
            .inputs li {{
              display: flex;
              align-items: center;
              justify-content: space-between;
              gap: 12px;
              padding: 10px 12px;
              border-radius: 12px;
              background: rgba(30, 41, 59, 0.62);
              border: 1px solid rgba(148, 163, 184, 0.12);
            }}
            .inputs span {{ color: #cbd5e1; }}
            .inputs strong {{ color: #f8fafc; }}
            #network {{ height: calc(100vh - 160px); min-height: 760px; border-radius: 20px; overflow: hidden; }}
            .graph-shell {{ padding: 12px; }}
            .footnote {{ margin-top: 14px; color: var(--muted); font-size: 13px; line-height: 1.5; }}
            @media (max-width: 1080px) {{
              main {{ grid-template-columns: 1fr; }}
              #network {{ height: 78vh; min-height: 640px; }}
            }}
          </style>
        </head>
        <body>
          <header>
            <h1>Composio dependency graph</h1>
            <p class="subtitle">
              Bipartite graph of Google Super and GitHub tool relationships. Tool nodes connect to canonical dependency entities,
              which makes it easier to see when an action needs upstream discovery work versus direct user input.
            </p>
          </header>
          <main>
            <section class="panel summary">
              <div class="summary-grid">
                <div class="card"><div class="label">Tools</div><div class="value">{stats['tools']}</div></div>
                <div class="card"><div class="label">Entities</div><div class="value">{stats['entities']}</div></div>
                <div class="card"><div class="label">Edges</div><div class="value">{stats['edges']}</div></div>
                <div class="card"><div class="label">User fallbacks</div><div class="value">{len(unresolved)}</div></div>
              </div>

              <div class="section">
                <h2>Toolkit coverage</h2>
                <div class="legend">
                  <div class="legend-item"><div class="left"><span class="swatch" style="background:{TOOLKIT_STYLES['googlesuper']['color']}"></span><span>Google Super tools</span></div><strong>{stats['toolkits']['googlesuper']}</strong></div>
                  <div class="legend-item"><div class="left"><span class="swatch" style="background:{TOOLKIT_STYLES['github']['color']}"></span><span>GitHub tools</span></div><strong>{stats['toolkits']['github']}</strong></div>
                  <div class="legend-item"><div class="left"><span class="swatch" style="background:{TOOLKIT_STYLES['entity']['color']}"></span><span>Dependency entities</span></div><strong>{stats['entities']}</strong></div>
                  <div class="legend-item"><div class="left"><span class="swatch" style="background:{TOOLKIT_STYLES['user']['color']}"></span><span>User fallback</span></div><strong>1</strong></div>
                </div>
              </div>

              <div class="section">
                <h2>Most common unresolved inputs</h2>
                <ul class="inputs">{unresolved_markup}</ul>
                <div class="footnote">
                  These are required values that do not appear to have a strong upstream producer tool, so they should usually come from the user.
                </div>
              </div>
            </section>

            <section class="panel graph-shell">
              <div id="network"></div>
            </section>
          </main>

          <script>
            const graph = {graph_json};
            const nodes = new vis.DataSet(graph.nodes.map((node) => {{
              const palette = node.group === 'googlesuper'
                ? {json.dumps(TOOLKIT_STYLES['googlesuper'])}
                : node.group === 'github'
                  ? {json.dumps(TOOLKIT_STYLES['github'])}
                  : node.group === 'user'
                    ? {json.dumps(TOOLKIT_STYLES['user'])}
                    : {json.dumps(TOOLKIT_STYLES['entity'])};

              const shape = node.shape || (node.type === 'tool' ? 'box' : node.type === 'user' ? 'diamond' : 'ellipse');
              return {{
                id: node.id,
                label: node.label,
                title: node.title,
                shape,
                color: {{
                  background: palette.color,
                  border: palette.border,
                  highlight: {{ background: palette.color, border: palette.border }},
                  hover: {{ background: palette.color, border: palette.border }},
                }},
                font: {{ color: '#0f172a', size: 13, face: 'ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif' }},
                margin: 10,
              }};
            }}));

            const edges = new vis.DataSet(graph.edges.map((edge) => ({{
              from: edge.from,
              to: edge.to,
              label: edge.label,
              title: edge.title,
              arrows: edge.arrows || 'to',
              dashes: Boolean(edge.dashes),
              width: 1.4,
              smooth: {{ type: 'dynamic' }},
              font: {{ align: 'middle', size: 10, color: '#cbd5e1', strokeWidth: 0 }},
              color: edge.color || {{ color: '#94a3b8', highlight: '#e2e8f0' }},
            }})));

            const container = document.getElementById('network');
            const network = new vis.Network(container, {{ nodes, edges }}, {{
              autoResize: true,
              interaction: {{ hover: true, navigationButtons: true, keyboard: true, multiselect: true }},
              layout: {{ improvedLayout: true }},
              physics: {{
                enabled: true,
                solver: 'forceAtlas2Based',
                stabilization: {{ iterations: 180, fit: true }},
                forceAtlas2Based: {{
                  gravitationalConstant: -65,
                  centralGravity: 0.03,
                  springLength: 140,
                  springConstant: 0.12,
                  damping: 0.42,
                }},
              }},
              edges: {{ arrows: {{ to: {{ enabled: true, scaleFactor: 0.85 }} }} }},
            }});

            network.once('stabilizationIterationsDone', () => {{
              network.setOptions({{ physics: false }});
            }});
          </script>
        </body>
        </html>
        """
    )


def main() -> None:
    print("Fetching Composio tool metadata...")
    tools = load_tools()
    print(f"Loaded {len(tools)} tools")

    graph = build_graph(tools)
    OUTPUT_JSON.write_text(json.dumps(graph, indent=2), encoding="utf-8")
    OUTPUT_HTML.write_text(render_html(graph), encoding="utf-8")

    print(f"Wrote {OUTPUT_JSON}")
    print(f"Wrote {OUTPUT_HTML}")
    print("Top unresolved user-only inputs:")
    for name, count in graph["stats"]["unresolved_user_inputs"][:12]:
        print(f"  {name}: {count}")


if __name__ == "__main__":
    main()