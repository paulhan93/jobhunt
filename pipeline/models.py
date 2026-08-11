from dataclasses import dataclass, field


@dataclass
class NormalizedJob:
    """One posting, in our shape rather than the ATS's."""
    source: str
    source_job_id: str
    title: str
    location: str | None = None
    remote: bool | None = None
    description: str | None = None
    apply_url: str | None = None
    comp_min: float | None = None
    comp_max: float | None = None
    comp_currency: str | None = None
    raw: dict = field(default_factory=dict)
