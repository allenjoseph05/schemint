#!/usr/bin/env python3
"""
Run all test schemas through Schemint analysis.

Usage:
    python examples/test_schemas/run_all_tests.py
    python examples/test_schemas/run_all_tests.py --verbose
    python examples/test_schemas/run_all_tests.py --file 03_security_risks.sql
    python examples/test_schemas/run_all_tests.py --json
"""

import argparse
import json
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from schemint.core.analyzer import analyze_sql
from schemint.core.parser import parse_sql
from schemint.ci.ingest import CIIngestHandler
from schemint.ci.report_builder import CIReportBuilder
from schemint.ci.models import (
    AnalysisDecision,
    AnalysisFinding,
    DecisionStatus,
    FindingLocation,
)


def analyze_file(filepath: Path, verbose: bool = False) -> dict:
    """Analyze a single SQL file and return results."""
    sql = filepath.read_text(encoding="utf-8")

    # Split: files with only ALTER/DROP can't be parsed by the schema analyzer
    # but can be checked for dangerous patterns
    has_create = "CREATE TABLE" in sql.upper()
    has_alter_or_drop = "ALTER TABLE" in sql.upper() or "DROP TABLE" in sql.upper()

    result_data = {
        "file": filepath.name,
        "score": None,
        "grade": None,
        "label": None,
        "critical": 0,
        "warning": 0,
        "suggestion": 0,
        "issues": [],
        "dangerous_patterns": [],
    }

    # Check for dangerous patterns (works on ALTER/DROP)
    if has_alter_or_drop:
        handler = CIIngestHandler()
        dangerous = handler._check_dangerous_patterns(sql, filepath.name)
        for d in dangerous:
            result_data["dangerous_patterns"].append({
                "type": d.type,
                "severity": d.severity,
                "title": d.title,
            })
            if d.severity == "critical":
                result_data["critical"] += 1
            elif d.severity == "warning":
                result_data["warning"] += 1

    # Run full schema analysis (only works with CREATE TABLE)
    if has_create:
        try:
            result = analyze_sql(sql)
            result_data["score"] = result.score.total
            result_data["grade"] = result.score.grade
            result_data["label"] = result.score.label

            for issue in result.issues:
                result_data["issues"].append({
                    "severity": issue.severity.value,
                    "category": issue.category.value,
                    "title": issue.title,
                    "table": issue.table_name,
                    "column": issue.column_name,
                })

            result_data["critical"] += result.critical_count
            result_data["warning"] += result.warning_count
            result_data["suggestion"] += result.suggestion_count

        except Exception as e:
            result_data["issues"].append({
                "severity": "error",
                "category": "parse_error",
                "title": f"Failed to parse: {e}",
                "table": None,
                "column": None,
            })

    # Build CI report if we have analysis results
    if has_create and result_data["score"] is not None:
        try:
            result = analyze_sql(sql)
            builder = CIReportBuilder()

            # Build findings for annotations
            findings = []
            for issue in result.issues:
                findings.append(AnalysisFinding(
                    type=issue.category.value,
                    severity=issue.severity.value,
                    title=issue.title,
                    description=issue.description or "",
                    location=FindingLocation(
                        file=filepath.name,
                        table=issue.table_name,
                        column=issue.column_name,
                    ),
                ))

            # Add dangerous patterns as findings
            if has_alter_or_drop:
                handler = CIIngestHandler()
                dangerous = handler._check_dangerous_patterns(sql, filepath.name)
                findings.extend(dangerous)

            # Determine status
            active = [f for f in findings if not getattr(f, 'suppressed_by_memory', False)]
            if any(f.severity == "critical" for f in active):
                status = DecisionStatus.FAIL
            elif any(f.severity == "warning" for f in active):
                status = DecisionStatus.WARN
            else:
                status = DecisionStatus.PASS

            result_data["ci_status"] = status.value

            # Build score
            score = builder.build_score([result])
            result_data["ci_score"] = {
                "total": score.total,
                "grade": score.grade,
                "structural": score.structural,
                "performance": score.performance,
                "naming": score.naming,
                "best_practices": score.best_practices,
            }
        except Exception:
            pass

    return result_data


def print_result(data: dict, verbose: bool = False):
    """Pretty-print a single file's results."""
    name = data["file"]
    score = data["score"]
    grade = data["grade"]
    label = data["label"]
    critical = data["critical"]
    warning = data["warning"]
    suggestion = data["suggestion"]
    ci_status = data.get("ci_status", "n/a")

    # Status indicator
    if critical > 0:
        indicator = "FAIL"
    elif warning > 0:
        indicator = "WARN"
    else:
        indicator = "PASS"

    score_str = f"{score}/100 ({grade})" if score is not None else "N/A"

    print(f"\n{'=' * 70}")
    print(f"  {name}")
    print(f"  Score: {score_str}  |  CI Status: {ci_status.upper()}  |  {indicator}")
    print(f"  Critical: {critical}  Warning: {warning}  Suggestion: {suggestion}")
    print(f"{'=' * 70}")

    if verbose:
        if data["dangerous_patterns"]:
            print("\n  Dangerous Patterns:")
            for dp in data["dangerous_patterns"]:
                print(f"    [{dp['severity'].upper()}] {dp['type']}: {dp['title']}")

        if data["issues"]:
            print("\n  Issues:")
            for issue in data["issues"]:
                sev = issue["severity"].upper()
                cat = issue["category"]
                title = issue["title"]
                loc = issue.get("table", "")
                if issue.get("column"):
                    loc += f".{issue['column']}"
                print(f"    [{sev}] {cat}: {title}")
                if loc:
                    print(f"           Location: {loc}")

        if data.get("ci_score"):
            cs = data["ci_score"]
            print(f"\n  Score Breakdown:")
            print(f"    Structural:     {cs['structural']}/100")
            print(f"    Performance:    {cs['performance']}/100")
            print(f"    Naming:         {cs['naming']}/100")
            print(f"    Best Practices: {cs['best_practices']}/100")


def main():
    parser = argparse.ArgumentParser(description="Run Schemint test schemas")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show detailed output")
    parser.add_argument("--file", "-f", type=str, help="Run a specific file only")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    schema_dir = Path(__file__).parent
    sql_files = sorted(schema_dir.glob("*.sql"))

    if args.file:
        sql_files = [f for f in sql_files if args.file in f.name]
        if not sql_files:
            print(f"No files matching '{args.file}' found")
            return 1

    if not sql_files:
        print("No SQL files found in examples/test_schemas/")
        return 1

    print(f"\nSchemint Schema Analyzer — Test Suite")
    print(f"Running {len(sql_files)} test schemas...\n")

    all_results = []
    total_critical = 0
    total_warning = 0
    total_suggestion = 0

    for filepath in sql_files:
        data = analyze_file(filepath, verbose=args.verbose)
        all_results.append(data)
        total_critical += data["critical"]
        total_warning += data["warning"]
        total_suggestion += data["suggestion"]

        if not args.json:
            print_result(data, verbose=args.verbose)

    if args.json:
        print(json.dumps(all_results, indent=2))
        return 0

    # Summary
    print(f"\n{'=' * 70}")
    print(f"  SUMMARY: {len(sql_files)} files analyzed")
    print(f"  Total Critical: {total_critical}")
    print(f"  Total Warnings: {total_warning}")
    print(f"  Total Suggestions: {total_suggestion}")
    print(f"{'=' * 70}")

    # Print score table
    print(f"\n  {'File':<40} {'Score':>6} {'Grade':>6} {'Status':>8}")
    print(f"  {'-'*40} {'-'*6} {'-'*6} {'-'*8}")
    for r in all_results:
        score = f"{r['score']}" if r['score'] is not None else "N/A"
        grade = r['grade'] or "N/A"
        if r["critical"] > 0:
            status = "FAIL"
        elif r["warning"] > 0:
            status = "WARN"
        else:
            status = "PASS"
        print(f"  {r['file']:<40} {score:>6} {grade:>6} {status:>8}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
