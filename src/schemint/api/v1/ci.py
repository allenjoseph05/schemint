"""
CI Integration API Endpoints.

Primary interface for CI/CD integration.
"""

from fastapi import APIRouter, HTTPException, status

from schemint.ci.ingest import CIIngestHandler, ingest_ci_event
from schemint.ci.models import (
    AnalysisDecision,
    CIEventType,
    CIIngestRequest,
    GitProvider,
)
from schemint.config import get_settings

router = APIRouter()


@router.post("/ingest", response_model=AnalysisDecision)
async def ingest_ci(
    request: CIIngestRequest,
) -> AnalysisDecision:
    """
    Ingest a CI event for schema analysis.

    This is the PRIMARY entry point for CI integration.
    Analysis is always AI-powered. Requires `CLAUDE_API_KEY`.

    **Triggered by:** GitHub Actions, GitLab CI, Jenkins, etc.

    **Flow:**
    1. Validate/register project
    2. Fetch diff from git provider
    3. Extract SQL changes
    4. Run AI-powered analysis pipeline
    5. Update CI status
    6. Return decision with findings

    **Example Request:**
    ```json
    {
        "project_id": "github:acme/ecommerce",
        "event_type": "pull_request",
        "ref": "abc123def",
        "base_ref": "main",
        "provider": "github",
        "provider_token": "ghp_xxxx",
        "pr_number": 123
    }
    ```

    **Decision Status:**
    - `pass`: No blocking issues found
    - `warn`: Warnings found but not blocking
    - `fail`: Critical issues that should block
    - `error`: Analysis failed

    **Memory Integration:**
    - Previously accepted findings are suppressed
    - Business rules can modify severity
    - `suppressed_count` shows how many were suppressed
    """
    try:
        # Validate AI availability
        settings = get_settings()
        if not settings.ai_enabled:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="CLAUDE_API_KEY is not configured. AI analysis requires a valid API key.",
            )

        decision = await ingest_ci_event(request)
        return decision

    except HTTPException:
        raise

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"CI ingestion failed: {e!s}",
        )


@router.post("/webhook/github")
async def github_webhook(payload: dict) -> dict:
    """
    GitHub webhook endpoint for automated triggers.

    Configure your GitHub repository to send webhooks here for:
    - `pull_request` events
    - `push` events

    **Setup:**
    1. Go to repo Settings > Webhooks > Add webhook
    2. Payload URL: `https://your-schemint-url/api/v1/ci/webhook/github`
    3. Content type: `application/json`
    4. Select events: `Pull requests` and `Pushes`

    Note: Requires `GITHUB_WEBHOOK_SECRET` for signature verification.
    """
    # TODO: Implement webhook signature verification
    # TODO: Parse GitHub webhook payload format

    event_type = payload.get("action", "push")
    repo = payload.get("repository", {}).get("full_name", "")

    if not repo:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing repository information",
        )

    # Build ingest request from webhook
    request = CIIngestRequest(
        project_id=f"github:{repo}",
        event_type=CIEventType.PULL_REQUEST if "pull_request" in payload else CIEventType.PUSH,
        ref=payload.get("pull_request", {}).get("head", {}).get("sha", "")
        or payload.get("after", ""),
        base_ref=payload.get("pull_request", {}).get("base", {}).get("ref", "main")
        or payload.get("before", "main"),
        provider=GitProvider.GITHUB,
        pr_number=payload.get("pull_request", {}).get("number"),
        pr_title=payload.get("pull_request", {}).get("title"),
    )

    decision = await ingest_ci_event(request)

    return {
        "decision_id": decision.decision_id,
        "status": decision.status.value,
        "findings_count": len(decision.findings),
    }


@router.post("/webhook/gitlab")
async def gitlab_webhook(payload: dict) -> dict:
    """
    GitLab webhook endpoint for automated triggers.

    Configure your GitLab project to send webhooks here for:
    - Merge request events
    - Push events

    **Setup:**
    1. Go to project Settings > Webhooks
    2. URL: `https://your-schemint-url/api/v1/ci/webhook/gitlab`
    3. Select triggers: `Merge request events`, `Push events`
    """
    # TODO: Implement webhook token verification
    # TODO: Parse GitLab webhook payload format

    object_kind = payload.get("object_kind", "push")
    project = payload.get("project", {})
    repo = project.get("path_with_namespace", "")

    if not repo:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing project information",
        )

    # Determine event type and refs
    if object_kind == "merge_request":
        mr = payload.get("object_attributes", {})
        ref = mr.get("last_commit", {}).get("id", "")
        base_ref = mr.get("target_branch", "main")
        event_type = CIEventType.PULL_REQUEST
    else:
        ref = payload.get("after", "")
        base_ref = payload.get("before", "main")
        event_type = CIEventType.PUSH

    request = CIIngestRequest(
        project_id=f"gitlab:{repo}",
        event_type=event_type,
        ref=ref,
        base_ref=base_ref,
        provider=GitProvider.GITLAB,
    )

    decision = await ingest_ci_event(request)

    return {
        "decision_id": decision.decision_id,
        "status": decision.status.value,
        "findings_count": len(decision.findings),
    }


@router.get("/status/{decision_id}")
async def get_decision_status(decision_id: str) -> dict:
    """
    Get status of a previous analysis decision.

    Use this to check on the status of an analysis or
    retrieve the full decision details.
    """
    # TODO: Store and retrieve decisions
    # For now, decisions are not persisted beyond the response
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Decision {decision_id} not found. Note: Decision persistence not yet implemented.",
    )
