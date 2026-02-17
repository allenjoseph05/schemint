"""
Schemint - AI-powered database schema linter and analyzer.

Usage:
    from schemint import analyze

    result = analyze("CREATE TABLE users (id INT, name VARCHAR(100));")
    print(result.score)
    for issue in result.issues:
        print(f"{issue.severity}: {issue.title}")
"""

__version__ = "0.1.0"
__author__ = "Your Name"

from schemint.core.analyzer.analyzer import analyze, analyze_sql
from schemint.models.analysis import AnalysisResult
from schemint.models.issue import Issue, IssueSeverity

__all__ = [
    "AnalysisResult",
    "Issue",
    "IssueSeverity",
    "__version__",
    "analyze",
    "analyze_sql",
]
