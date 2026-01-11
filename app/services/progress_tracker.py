"""Progress tracking service for SSE streaming."""

import asyncio
import json
import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import AsyncGenerator, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class ProgressEvent:
    """A progress event to be streamed via SSE."""
    stage: str
    progress: int  # 0-100
    message: str
    detail: Optional[str] = None
    timestamp: Optional[str] = None

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_sse(self) -> str:
        """Format as SSE data."""
        data = json.dumps(asdict(self))
        return f"data: {data}\n\n"


class ProgressTracker:
    """Tracks progress for a specific task and enables SSE streaming."""

    # Define processing stages with their progress ranges
    STAGES = {
        "initializing": (0, 5),
        "loading_documents": (5, 10),
        "classifying": (10, 15),
        "extracting_batch": (15, 60),  # Batch processing for financial docs
        "merging_batches": (60, 65),   # Combining batch results
        "verification": (65, 70),       # Verification pass
        "reading_transactions": (70, 75),
        "applying_feedback": (75, 78),
        "querying_rag": (78, 82),
        "categorizing": (82, 90),
        "applying_tax_rules": (90, 94),
        "generating_summaries": (94, 98),
        "finalizing": (98, 100),
        "complete": (100, 100),
        "error": (0, 0),
    }

    def __init__(self, task_id: str):
        self.task_id = task_id
        self.queue: asyncio.Queue[ProgressEvent] = asyncio.Queue()
        self.current_stage = "initializing"
        self.current_progress = 0
        self.is_complete = False
        self.error: Optional[str] = None

    async def emit(self, stage: str, message: str, detail: Optional[str] = None, sub_progress: float = 0.0):
        """
        Emit a progress event.

        Args:
            stage: Current processing stage
            message: Human-readable message
            detail: Optional detail text
            sub_progress: Progress within the current stage (0.0 to 1.0)
        """
        self.current_stage = stage

        if stage in self.STAGES:
            start, end = self.STAGES[stage]
            self.current_progress = int(start + (end - start) * sub_progress)

        event = ProgressEvent(
            stage=stage,
            progress=self.current_progress,
            message=message,
            detail=detail
        )

        await self.queue.put(event)
        logger.debug(f"Progress [{self.task_id}]: {stage} - {self.current_progress}% - {message}")

    async def complete(self, detail: Optional[str] = None, message: str = "Processing complete"):
        """Mark processing as complete."""
        self.current_progress = 100

        event = ProgressEvent(
            stage="complete",
            progress=100,
            message=message,
            detail=detail
        )
        logger.info(f"Emitting complete event: detail={detail}, message={message}")
        # Put event in queue BEFORE setting is_complete to avoid race condition
        await self.queue.put(event)
        self.is_complete = True

    async def fail(self, error: str):
        """Mark processing as failed."""
        self.error = error

        event = ProgressEvent(
            stage="error",
            progress=self.current_progress,
            message="Processing failed",
            detail=error
        )
        # Put event in queue BEFORE setting is_complete to avoid race condition
        await self.queue.put(event)
        self.is_complete = True

    async def stream(self) -> AsyncGenerator[str, None]:
        """Generate SSE events for streaming."""
        while True:
            try:
                # Wait for next event with timeout
                event = await asyncio.wait_for(self.queue.get(), timeout=30.0)
                sse_data = event.to_sse()
                logger.info(f"Streaming event: stage={event.stage}, progress={event.progress}")
                yield sse_data

                # Only break AFTER yielding the complete/error event itself
                if event.stage in ("complete", "error"):
                    logger.info(f"Stream ending after {event.stage} event")
                    break

            except asyncio.TimeoutError:
                # Send keepalive
                yield ": keepalive\n\n"

                # Only break on timeout if we're complete AND queue is empty
                if self.is_complete and self.queue.empty():
                    logger.info("Stream complete after timeout, breaking loop")
                    break


# Global registry of active progress trackers
_active_trackers: Dict[str, ProgressTracker] = {}


def create_tracker(task_id: str) -> ProgressTracker:
    """Create and register a new progress tracker."""
    tracker = ProgressTracker(task_id)
    _active_trackers[task_id] = tracker
    return tracker


def get_tracker(task_id: str) -> Optional[ProgressTracker]:
    """Get an existing progress tracker."""
    return _active_trackers.get(task_id)


def remove_tracker(task_id: str):
    """Remove a progress tracker from the registry."""
    if task_id in _active_trackers:
        del _active_trackers[task_id]
