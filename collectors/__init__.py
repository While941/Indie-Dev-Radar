"""Data collection layer — one module per source."""
from __future__ import annotations

from .base import Collector
from .github_collector import GitHubCollector
from .godot_collector import GodotCollector
from .hackernews_collector import HackerNewsCollector

__all__ = ["Collector", "GitHubCollector", "GodotCollector", "HackerNewsCollector"]
