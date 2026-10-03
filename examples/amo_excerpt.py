import json, os, time
from collections import defaultdict
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError

base = f"https://{os.environ['AMO_SUBDOMAIN']}.amocrm.ru/api/v4"
auth = {"Authorization": f"Bearer {os.environ['AMO_ACCESS_TOKEN']}"}

def collect(resource, **filters):
    rows, page = [], 1
    while True:
        query = urlencode({"limit": 250, "page": page, **filters})
        req = Request(f"{base}/{resource}?{query}", headers=auth)
        for attempt in range(4):
            try:
                time.sleep(0.2)
                with urlopen(req, timeout=30) as response:
                    if response.status == 204:
                        return rows
                    data = json.load(response)
                break
            except HTTPError as error:
                if error.code not in (429, 500, 502, 503, 504) or attempt == 3:
                    raise
                time.sleep(2 ** attempt)
        rows.extend(data["_embedded"][resource])
        if "next" not in data.get("_links", {}):
            return rows
        page += 1

now = int(time.time())
leads = collect("leads", **{"order[id]": "asc"})
tasks = collect("tasks", **{"filter[entity_type]": "leads",
                            "filter[is_completed]": 0, "order[id]": "asc"})
by_lead = defaultdict(list)
for task in tasks:
    by_lead[task["entity_id"]].append(task)
result = []
for lead in leads:
    if lead["status_id"] in (142, 143):
        continue
    active = by_lead[lead["id"]]
    overdue = [t["id"] for t in active if t["complete_till"] < now]
    if not active or overdue:
        result.append({"lead_id": lead["id"], "no_open_tasks": not active,
                       "overdue_task_ids": overdue})
print(json.dumps(result, ensure_ascii=False, indent=2))
