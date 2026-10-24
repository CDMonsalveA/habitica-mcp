"""habitica-mcp: an MCP server that connects LLMs with Habitica."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("habitica-mcp")
except PackageNotFoundError:  # pragma: no cover - only when running from an uninstalled tree
    __version__ = "0.1.0"

__all__ = ["__version__"]
