"""Schemint evaluation harness.

Measures schemint's migration-safety decisions against ground truth generated
by applying migrations to a real PostgreSQL database.

Not packaged into the wheel — run from the repo root against the editable
install (``python -m evals.run``, ``python -m evals.generate_truth``).

Layout:
    evals.core      models, keys, storage, metering, aggregation
    evals.oracle    real-Postgres ground-truth generation
    evals.adapters  systems under test
    evals.scorers   metric computation
    evals.suites    task fixtures (SQL + meta.json + generated expected.json)
"""

__all__ = ["__version__"]

__version__ = "0.1.0"
