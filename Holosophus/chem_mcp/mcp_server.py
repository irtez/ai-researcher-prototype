"""MCP server exposing all chemistry tool wrappers in this project."""

from mcp.server.fastmcp import FastMCP

from adme_tools import TOOL_REGISTRY as ADME_TOOLS
from chemx_tools import TOOL_REGISTRY as CHEMX_TOOLS
from dataset_tools import TOOL_REGISTRY as DATASET_TOOLS
from file_tools import TOOL_REGISTRY as FILE_TOOLS
from molecular_structure_tools import TOOL_REGISTRY as MOLECULAR_STRUCTURE_TOOLS
from pubchem_tools import TOOL_REGISTRY as PUBCHEM_TOOLS

SERVER_NAME = "chem-tools"
SERVER_INSTRUCTIONS = (
    "Use these tools for chemistry workflows backed by the local project: "
    "list_datasets and describe_dataset to select and understand registered sources, "
    "query_dataset_sql for table queries and search_connected_datasets for chemical "
    "identity/substructure in server datasets or session files. Also available: "
    "lab report ingestion and grounded reporting from CSV/DOCX/XLS/XLSX files, "
    "PubChem search and identifier conversion, molecule physchem plus ADME profiling "
    "with heuristic and ADMET-AI neural-network modes, and connected dataset search."
)

TOOL_REGISTRY = {
    **ADME_TOOLS,
    **DATASET_TOOLS,
    **FILE_TOOLS,
    **PUBCHEM_TOOLS,
    **MOLECULAR_STRUCTURE_TOOLS,
}


class ChemistryMCP(FastMCP):
    """Keep old calls resumable without advertising a duplicate dataset catalog."""

    async def list_tools(self):
        return [tool for tool in await super().list_tools() if tool.name not in CHEMX_TOOLS]


def build_mcp_server() -> FastMCP:
    """Register current tools plus hidden compatibility handlers for old calls."""
    server = ChemistryMCP(
        SERVER_NAME,
        instructions=SERVER_INSTRUCTIONS,
    )

    for tool_name, tool_fn in {**TOOL_REGISTRY, **CHEMX_TOOLS}.items():
        server.add_tool(
            tool_fn,
            name=tool_name,
            description=tool_fn.__doc__,
        )

    return server


def main() -> None:
    """Run the MCP server over stdio."""
    build_mcp_server().run(transport="stdio")


if __name__ == "__main__":
    main()
