"""Independent source adapters with the same search interface."""

from .arxiv import ArxivTool
from .wikipedia import WikipediaTool

__all__ = ["ArxivTool", "WikipediaTool"]
