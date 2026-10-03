"""Deterministic rules for the current CRM snapshot."""
from collections import defaultdict


def problem_deals(leads, tasks, as_of):
    by_lead = defaultdict(list)
    for task in tasks:
        if task.get("entity_type") == "leads" and not task["is_completed"]:
            by_lead[task["entity_id"]].append(task)

    result = []
    for lead in leads:
        if lead["status_id"] in (142, 143) or lead.get("is_deleted", False):
            continue
        active = by_lead[lead["id"]]
        overdue = sorted({task["id"] for task in active
                          if task["complete_till"] < as_of})
        if not active or overdue:
            result.append({
                "lead_id": lead["id"],
                "responsible_user_id": lead.get("responsible_user_id"),
                "no_open_tasks": not active,
                "overdue_task_ids": overdue,
            })
    return sorted(result, key=lambda item: item["lead_id"])
