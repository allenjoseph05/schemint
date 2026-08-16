"""Trajectory-level evaluation for the multi-turn schema-review agent.

This package is intentionally separate from ``evals.core``. Migration evals
compare predicted database changes with PostgreSQL reality; agent evals compare
schema-review findings and tool trajectories with versioned review contracts.
"""
