import asyncio
import functools
import inspect
import json
import os
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from functools import partial
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import quote_plus

import anyio
import httpx
from mcp import ClientSession, Tool
from mcp.client.streamable_http import streamablehttp_client
from mcp.types import CallToolResult, ContentBlock, TextContent
from pydantic import ValidationError
import requests
import xmltodict

AGENT_TIMEOUT = int(os.getenv("AGENT_TIMEOUT", 24 * 60 * 60))
TOOL_TIMEOUT = int(os.getenv("TOOL_TIMEOUT", 12 * 60 * 60))
SERVER_URL_TEMPLATE = os.getenv("SERVER_URL_TEMPLATE", "http://host.docker.internal:{port}")
ARXIV_BASE_URL = "http://export.arxiv.org"
ARXIV_URL_TEMPLATE = (
    "{base_url}/api/query?search_query={query}&start={start}&sortBy={sort_by}"
    "&sortOrder={sort_order}&max_results={limit}"
)


ToolReturnType = Any

_tool_schemas: Dict[str, Tool] = {}


def _schema_type_names(field_schema: dict[str, Any]) -> set[str]:
    types: set[str] = set()
    field_type = field_schema.get("type")
    if isinstance(field_type, str):
        types.add(field_type)
    for option in field_schema.get("anyOf", []):
        if isinstance(option, dict):
            option_type = option.get("type")
            if isinstance(option_type, str):
                types.add(option_type)
    return types


def _coerce_value_for_schema(field_schema: dict[str, Any], value: Any) -> Any:
    if value is None:
        return None
    accepted_types = _schema_type_names(field_schema)
    if not accepted_types:
        return value
    if "array" in accepted_types and isinstance(value, tuple):
        return list(value)
    if "object" in accepted_types and hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if "string" in accepted_types and not isinstance(value, str):
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False)
        return str(value)
    return value


def _coerce_arguments_to_schema(
    tool_schema: Optional[Tool],
    arguments: Dict[str, Any],
) -> Dict[str, Any]:
    has_input_schema = (
        tool_schema is not None
        and hasattr(tool_schema, "inputSchema")
        and tool_schema.inputSchema
        and isinstance(tool_schema.inputSchema, dict)
        and "properties" in tool_schema.inputSchema
    )
    if not has_input_schema:
        return arguments

    assert tool_schema
    properties = tool_schema.inputSchema["properties"]
    coerced: Dict[str, Any] = {}
    for key, value in arguments.items():
        field_schema = properties.get(key)
        if isinstance(field_schema, dict):
            coerced[key] = _coerce_value_for_schema(field_schema, value)
        else:
            coerced[key] = value
    return coerced


def _extract_tool_error(result: CallToolResult) -> str | None:
    if not result.isError:
        return None
    text_chunks = [
        block.text
        for block in result.content
        if isinstance(block, TextContent) and isinstance(block.text, str) and block.text.strip()
    ]
    if text_chunks:
        return "\n".join(text_chunks)
    if result.structuredContent:
        return json.dumps(result.structuredContent, ensure_ascii=False)
    return "Unknown MCP tool error"


def _text_value(raw: Any, default: str = "") -> str:
    if raw is None:
        return default
    if isinstance(raw, dict):
        for key in ("#text", "text"):
            value = raw.get(key)
            if value is not None:
                return str(value)
        return default
    return str(raw)


def _normalize_text(text: str) -> str:
    return " ".join(part.strip() for part in text.split() if part.strip())


def _format_authors(authors: Any) -> str:
    if not authors:
        return ""
    if isinstance(authors, dict):
        authors = [authors]
    names = []
    for author in authors:
        if isinstance(author, dict):
            names.append(_text_value(author.get("name")))
        elif author:
            names.append(str(author))
    names = [name for name in names if name]
    result = ", ".join(names[:3])
    if len(names) > 3:
        result += f", and {len(names) - 3} more authors"
    return result


def _format_categories(categories: Any) -> str:
    if not categories:
        return ""
    if isinstance(categories, dict):
        categories = [categories]
    if not isinstance(categories, list):
        categories = [categories]
    clean_categories = []
    for category in categories:
        if isinstance(category, dict):
            clean_categories.append(_text_value(category.get("@term")))
        elif category:
            clean_categories.append(str(category))
    return ", ".join(item.strip() for item in clean_categories if item and item.strip())


def _format_date(value: Any) -> str:
    raw = _text_value(value)
    dt = datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ")
    return dt.strftime("%B %d, %Y")


def _convert_to_yyyymmddtttt(date_str: str) -> str:
    date_obj = datetime.strptime(date_str, "%Y-%m-%d")
    return date_obj.strftime("%Y%m%d") + "0000"


def _compose_arxiv_query(
    query: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> str:
    normalized = query.replace(" AND NOT ", " ANDNOT ")
    if "-" in normalized:
        normalized = f"({normalized}) OR ({normalized.replace('-', ' ')})"
    if start_date or end_date:
        start_date = start_date or "1900-01-01"
        end_date = end_date or date.today().strftime("%Y-%m-%d")
        date_filter = (
            f"[{_convert_to_yyyymmddtttt(start_date)} TO {_convert_to_yyyymmddtttt(end_date)}]"
        )
        normalized = f"({normalized}) AND submittedDate:{date_filter}"
    return quote_plus(normalized)


def _clean_arxiv_entry(
    entry: dict[str, Any], index: int, include_abstracts: bool
) -> dict[str, Any]:
    return {
        "id": _text_value(entry.get("id")).split("/")[-1],
        "title": _normalize_text(_text_value(entry.get("title"))),
        "authors": _format_authors(entry.get("author")),
        "published": _format_date(entry.get("published")),
        "updated": _format_date(entry.get("updated")),
        "categories": _format_categories(entry.get("category")),
        "comment": _normalize_text(_text_value(entry.get("arxiv:comment"))),
        "index": index,
        "abstract": (
            _normalize_text(_text_value(entry.get("summary"))) if include_abstracts else None
        ),
    }


def _parse_arxiv_response(
    content: bytes,
    include_abstracts: bool,
) -> dict[str, Any]:
    parsed_content = xmltodict.parse(content)
    feed = parsed_content.get("feed", {})
    total_results = int(_text_value(feed.get("opensearch:totalResults"), "0") or 0)
    start_index = int(_text_value(feed.get("opensearch:startIndex"), "0") or 0)
    entries = feed.get("entry", [])
    if isinstance(entries, dict):
        entries = [entries]
    if not isinstance(entries, list):
        entries = []
    results = [
        _clean_arxiv_entry(entry, start_index + entry_num, include_abstracts)
        for entry_num, entry in enumerate(entries)
        if isinstance(entry, dict)
    ]
    return {
        "total_count": total_results,
        "returned_count": len(results),
        "offset": start_index,
        "results": results,
    }


def _local_arxiv_search(
    query: str,
    offset: Optional[int] = 0,
    limit: Optional[int] = 5,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    sort_by: Optional[str] = "relevance",
    sort_order: Optional[str] = "descending",
    include_abstracts: Optional[bool] = False,
    session_id: Optional[str] = None,
) -> dict[str, Any]:
    del session_id
    assert isinstance(query, str) and query.strip(), "query must be a non-empty string"
    assert isinstance(offset, int), "offset should be an integer"
    assert isinstance(limit, int), "limit should be an integer"
    assert isinstance(sort_by, str), "sort_by should be a string"
    assert isinstance(sort_order, str), "sort_order should be a string"
    assert include_abstracts is not None, "include_abstracts must be bool"

    url = ARXIV_URL_TEMPLATE.format(
        base_url=ARXIV_BASE_URL,
        query=_compose_arxiv_query(query, start_date, end_date),
        start=offset,
        limit=limit,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    response = requests.get(url, timeout=60)
    response.raise_for_status()
    return _parse_arxiv_response(response.content, include_abstracts=include_abstracts)


LOCAL_TOOL_OVERRIDES: Dict[str, Callable[..., ToolReturnType]] = {
    "arxiv_search": _local_arxiv_search,
}


async def _message_handler(session: ClientSession, exc: Exception) -> None:
    """
    Custom message handler for ValidationError.
    See https://github.com/modelcontextprotocol/python-sdk/issues/1144.
    """
    await anyio.lowlevel.checkpoint()
    if isinstance(exc, ValidationError):
        session._task_group.cancel_scope.cancel()


def _compose_arguments(tool_schema: Optional[Tool], *args: Any, **kwargs: Any) -> Dict[str, Any]:
    arguments: Dict[str, Any] = dict(kwargs)
    if not args:
        has_input_schema = (
            tool_schema is not None
            and hasattr(tool_schema, "inputSchema")
            and tool_schema.inputSchema
            and isinstance(tool_schema.inputSchema, dict)
            and "properties" in tool_schema.inputSchema
        )
        if not has_input_schema:
            return arguments
        assert tool_schema
        allowed = set(tool_schema.inputSchema["properties"].keys())
        filtered = {key: value for key, value in arguments.items() if key in allowed}
        return _coerce_arguments_to_schema(tool_schema, filtered)
    has_input_schema = (
        tool_schema is not None
        and hasattr(tool_schema, "inputSchema")
        and tool_schema.inputSchema
        and isinstance(tool_schema.inputSchema, dict)
        and "properties" in tool_schema.inputSchema
    )
    if not has_input_schema:
        return arguments

    assert tool_schema
    allowed = set(tool_schema.inputSchema["properties"].keys())
    arguments = {key: value for key, value in arguments.items() if key in allowed}
    param_names = list(tool_schema.inputSchema["properties"].keys())
    for i, arg in enumerate(args):
        if i >= len(param_names):
            break
        param_name = param_names[i]
        if param_name not in arguments:
            arguments[param_name] = arg
    return _coerce_arguments_to_schema(tool_schema, arguments)


def bind_tool_identity(
    tool: Callable[..., ToolReturnType],
    *,
    session_id: str | None,
    agent_name: str | None,
) -> Callable[..., ToolReturnType]:
    identity = {}
    if session_id:
        identity["session_id"] = session_id
    if agent_name:
        identity["agent_name"] = agent_name
    if not identity:
        return tool

    signature = inspect.signature(tool)
    accepts_var_keyword = any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    )
    if accepts_var_keyword:
        accepted_identity = identity
    else:
        accepted_identity = {
            name: value
            for name, value in identity.items()
            if name in signature.parameters
            and signature.parameters[name].kind
            in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
        }

    @functools.wraps(tool)
    def protected_tool(*args: Any, **kwargs: Any) -> ToolReturnType:
        protected_arguments = {
            name: value for name, value in kwargs.items() if name not in identity
        }
        protected_arguments.update(accepted_identity)
        return tool(*args, **protected_arguments)

    return protected_tool


async def _acall(tool: str, tool_server_port: int, *args: Any, **kwargs: Any) -> ToolReturnType:
    tool_schema = _tool_schemas.get(tool)
    arguments = _compose_arguments(tool_schema, *args, **kwargs)
    local_tool = LOCAL_TOOL_OVERRIDES.get(tool)
    if local_tool is not None:
        return local_tool(**arguments)

    base_url = SERVER_URL_TEMPLATE.format(port=tool_server_port)
    async with streamablehttp_client(
        base_url + "/mcp",
        timeout=TOOL_TIMEOUT,
        sse_read_timeout=TOOL_TIMEOUT,
    ) as (
        read_stream,
        write_stream,
        _,
    ):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            session._message_handler = partial(_message_handler, session)

            result: CallToolResult = await session.call_tool(tool, arguments)
            error_text = _extract_tool_error(result)
            if error_text:
                raise RuntimeError(error_text)

            structured_content = result.structuredContent
            if structured_content:
                if "result" in structured_content and len(structured_content) == 1:
                    return structured_content["result"]
                return structured_content

            content_blocks: List[ContentBlock] = result.content
            if len(content_blocks) == 0:
                return None
            if len(content_blocks) == 1 and content_blocks[0].type == "text":
                return content_blocks[0].text
            return content_blocks


def _call(tool: str, tool_server_port: int, *args: Any, **kwargs: Any) -> ToolReturnType:
    def runner() -> ToolReturnType:
        return asyncio.run(_acall(tool, tool_server_port, *args, **kwargs))

    executor = ThreadPoolExecutor(max_workers=1)
    try:
        future = executor.submit(runner)
        return future.result(timeout=TOOL_TIMEOUT + 5)
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


async def fetch_tools(
    tool_server_port: Optional[int] = None,
) -> Dict[str, Callable[..., ToolReturnType]]:
    if not tool_server_port:
        return {}
    global _tool_schemas
    final_tools = {}
    base_url = SERVER_URL_TEMPLATE.format(port=tool_server_port)
    print("Tools server URL", base_url)
    try:
        async with streamablehttp_client(base_url + "/mcp") as (
            read_stream,
            write_stream,
            _,
        ):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                tools_response = await session.list_tools()
                tools: List[Tool] = tools_response.tools

                _tool_schemas = {tool.name: tool for tool in tools}

                for tool in tools:
                    local_tool = LOCAL_TOOL_OVERRIDES.get(tool.name)
                    if local_tool is not None:
                        tool_fn = local_tool
                    else:
                        tool_fn = functools.partial(_call, tool.name, tool_server_port)
                    final_tools[tool.name] = tool_fn
    except Exception:
        print("Failed to fetch MCP tools")
        print(traceback.format_exc())
        pass

    agent_cards = []
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(base_url + "/agents/list")
            response.raise_for_status()
            agent_cards = response.json()
    except Exception:
        print("Failed to fetch agents")
        print(traceback.format_exc())
        pass

    for card in agent_cards:
        agent_name = card["name"]
        url = base_url + f"/agents/{agent_name}"

        def create_call_agent(url: str) -> Callable[..., Any]:
            def _call_agent(query: str, session_id: str) -> Any:
                payload = {
                    "messages": [{"role": "user", "content": query}],
                    "session_id": session_id,
                    "stream": False,
                }
                resp = httpx.post(url, json=payload, timeout=AGENT_TIMEOUT)
                resp.raise_for_status()
                return resp.json()

            return _call_agent

        final_tools["agent__" + agent_name] = create_call_agent(url)

    return final_tools
