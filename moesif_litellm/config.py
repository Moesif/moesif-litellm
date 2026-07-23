import os
from dataclasses import dataclass, field
from typing import Callable, List, Optional


@dataclass
class MoesifConfig:
    # ── Required ──────────────────────────────────────────────────────────────
    application_id: str = ""

    # ── Batching ──────────────────────────────────────────────────────────────
    batch_size: int = 100
    flush_interval: int = 2
    max_queue_size: int = 50_000

    # ── Body capture ──────────────────────────────────────────────────────────
    capture_request_body: bool = True
    capture_response_body: bool = True
    request_max_body_size: int = 100_000
    response_max_body_size: int = 100_000

    # ── Masking ───────────────────────────────────────────────────────────────
    request_body_masks: List[str] = field(default_factory=list)
    response_body_masks: List[str] = field(default_factory=list)
    request_header_masks: List[str] = field(default_factory=list)
    response_header_masks: List[str] = field(default_factory=list)

    # ── Identity resolution ───────────────────────────────────────────────────
    identify_user: Optional[Callable] = None
    identify_company: Optional[Callable] = None
    authorization_user_id_field: str = "sub"
    authorization_company_id_field: Optional[str] = None

    # ── Hooks ─────────────────────────────────────────────────────────────────
    skip_event: Optional[Callable] = None
    mask_event_model: Optional[Callable] = None

    # ── Sampling ──────────────────────────────────────────────────────────────
    sample_rate: int = 100

    # ── API ───────────────────────────────────────────────────────────────────
    moesif_base_url: str = "https://api.moesif.net"

    # ── Debug ─────────────────────────────────────────────────────────────────
    debug: bool = False

    def __post_init__(self):
        if not self.application_id:
            self.application_id = os.environ.get("MOESIF_APPLICATION_ID", "")
        if not self.application_id:
            raise ValueError(
                "Moesif application_id is required. "
                "Pass it as MoesifLogger(application_id=...) or set MOESIF_APPLICATION_ID."
            )
        if not 0 <= self.sample_rate <= 100:
            raise ValueError("sample_rate must be between 0 and 100.")
