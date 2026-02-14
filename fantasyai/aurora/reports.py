# fantasyai/aurora/reports.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, Any, List
from .analyzer import Anomaly
from ..core.llm_client import LLMClient
import logging

logger = logging.getLogger(__name__)

@dataclass
class ResearchReport:
    title: str
    domain: str
    summary: str
    details_markdown: str

def format_anomaly_for_prompt(anomaly: Anomaly) -> str:
    return f"- [{anomaly.domain}] {anomaly.description} (score={anomaly.score})"

def generate_research_report(domain: str, anomalies: List[Anomaly]) -> ResearchReport:
    llm = LLMClient()
    bullet_list = "\n".join(format_anomaly_for_prompt(a) for a in anomalies)
    system = (
        "You are an expert scientific analyst. Write a concise, clear report "
        "for a non-technical but intelligent audience. Use markdown headings."
    )
    user = (
        f"Domain: {domain}\n\n"
        f"Here are the anomalies and patterns detected:\n{bullet_list}\n\n"
        "Please:\n"
        "1. Provide a 1–2 sentence title.\n"
        "2. Provide a clear summary.\n"
        "3. Provide a deeper markdown section with observations and implications."
    )

    logger.debug("Calling LLM for research report generation...")
    # TODO: uncomment once LLM client is implemented
    # response = llm.chat(system, user)
    response = "# Placeholder Aurora Report\n\nThis is a stub report."  # temp

    # For now we just split naïvely
    report = ResearchReport(
        title=f"Aurora {domain.title()} Snapshot",
        domain=domain,
        summary="Placeholder summary – replace with parsed LLM output.",
        details_markdown=response,
    )
    return report

def aurora_report_stage(ctx: Dict[str, Any]) -> Dict[str, Any]:
    anomalies: List[Anomaly] = ctx.get("anomalies", [])
    by_domain: Dict[str, List[Anomaly]] = {}

    for a in anomalies:
        by_domain.setdefault(a.domain, []).append(a)

    reports: List[ResearchReport] = []
    for domain, items in by_domain.items():
        report = generate_research_report(domain, items)
        reports.append(report)
        logger.info("Generated Aurora report for domain=%s", domain)

    ctx["reports"] = reports
    return ctx
