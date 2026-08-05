"""Breaking classification and risk-order scoring."""

from evals.core.models import (
    EvalAnalysis,
    EvalTask,
    RunConfig,
    ScoreRow,
    Truth,
    is_breaking,
    risk_index,
)
from evals.scorers.blast_radius import blast_radius_scores
from evals.scorers.fidelity import snapshot_fidelity


def score_analysis(
    task: EvalTask,
    truth: Truth,
    analysis: EvalAnalysis,
    config: RunConfig,
) -> ScoreRow:
    """Score one normalized analysis against generated ground truth."""
    true_breaking = truth.must_block or is_breaking(truth.risk)
    pred_breaking = analysis.is_breaking
    blast = blast_radius_scores(truth.blast_radius, analysis.blast_radius)
    fidelity = snapshot_fidelity(truth.real_post_snapshot, analysis.predicted_snapshot)
    return ScoreRow(
        task_id=task.id,
        category=task.category,
        adapter=config.adapter,
        config_hash=config.config_hash(),
        trial=config.trial,
        true_breaking=true_breaking,
        pred_breaking=pred_breaking,
        correct=true_breaking == pred_breaking,
        false_positive=not true_breaking and pred_breaking,
        false_negative=true_breaking and not pred_breaking,
        risk_exact_match=analysis.risk == truth.risk,
        underestimated=risk_index(analysis.risk) < risk_index(truth.risk),
        overestimated=risk_index(analysis.risk) > risk_index(truth.risk),
        blast_recall=blast.recall,
        blast_precision=blast.precision,
        blast_f1=blast.f1,
        blast_true_count=blast.true_count,
        blast_pred_count=blast.pred_count,
        blocked_correctly=analysis.blocked == truth.must_block,
        fidelity_pct=fidelity,
        cost_usd=analysis.cost_usd,
        latency_ms=analysis.latency_ms,
        errored=analysis.error is not None,
    )
