import io
import json
import os
import runpy
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse

from citycar_case.amo import AmoAPIError, AmoClient
from citycar_case.rules import problem_deals

ROOT = Path(__file__).resolve().parents[1]


def lead(number, status=123):
    return {"id": number, "status_id": status, "responsible_user_id": 10}


def task(number, entity, due, completed=False, entity_type="leads"):
    return {"id": number, "entity_id": entity, "complete_till": due,
            "is_completed": completed, "entity_type": entity_type}


class Response(io.BytesIO):
    def __init__(self, payload=None, status=200):
        super().__init__(json.dumps(payload).encode() if payload is not None else b"")
        self.status = status


def collection(resource, rows, more=False):
    return {"_embedded": {resource: rows},
            "_links": {"next": {"href": "unused"}} if more else {}}


class RulesTests(unittest.TestCase):
    def test_absent_and_completed_tasks_need_next_action(self):
        found = problem_deals([lead(1), lead(2)],
                              [task(22, 2, 900, completed=True)], 1000)
        self.assertEqual([row["lead_id"] for row in found], [1, 2])
        self.assertTrue(all(row["no_open_tasks"] for row in found))

    def test_future_task_does_not_hide_overdue_task(self):
        found = problem_deals([lead(1)],
                              [task(11, 1, 900), task(12, 1, 1200)], 1000)
        self.assertEqual(found[0]["overdue_task_ids"], [11])
        self.assertFalse(found[0]["no_open_tasks"])

    def test_future_and_exact_deadline_are_not_overdue(self):
        found = problem_deals([lead(1), lead(2)],
                              [task(11, 1, 1000), task(22, 2, 1200)], 1000)
        self.assertEqual(found, [])

    def test_closed_deals_are_excluded(self):
        found = problem_deals([lead(1, 142), lead(2, 143)],
                              [task(22, 2, 900)], 1000)
        self.assertEqual(found, [])

    def test_contact_task_does_not_count_as_deal_task(self):
        found = problem_deals([lead(1)],
                              [task(11, 1, 1200, entity_type="contacts")], 1000)
        self.assertTrue(found[0]["no_open_tasks"])


class APIClientTests(unittest.TestCase):
    def client(self, opener):
        return AmoClient("example", "fake-test-token", opener=opener, sleep=Mock())

    def test_pagination_and_query_are_complete(self):
        opener = Mock(side_effect=[
            Response(collection("leads", [lead(1)], more=True)),
            Response(collection("leads", [lead(2)])),
        ])
        client = self.client(opener)
        self.assertEqual([row["id"] for row in client.get_all("leads")], [1, 2])
        requests = [entry.args[0] for entry in opener.call_args_list]
        self.assertEqual([parse_qs(urlparse(req.full_url).query)["page"]
                          for req in requests], [["1"], ["2"]])
        self.assertTrue(all(req.get_method() == "GET" for req in requests))
        self.assertEqual(requests[0].headers["Authorization"], "Bearer fake-test-token")

    def test_204_is_empty_collection(self):
        self.assertEqual(self.client(Mock(return_value=Response(status=204))).get_all("tasks"), [])

    def test_401_is_failure_not_empty_data(self):
        opener = Mock(side_effect=HTTPError("https://example.amocrm.ru", 401, "unauthorized", {}, None))
        with self.assertRaises(AmoAPIError):
            self.client(opener).get_all("leads")
        self.assertEqual(opener.call_count, 1)

    def test_429_retries_and_honors_retry_after(self):
        opener = Mock(side_effect=[
            HTTPError("https://example.amocrm.ru", 429, "rate limit", {"Retry-After": "3"}, None),
            Response(collection("tasks", [])),
        ])
        client = self.client(opener)
        self.assertEqual(client.get_all("tasks"), [])
        self.assertEqual(opener.call_count, 2)
        self.assertIn(unittest.mock.call(3.0), client.sleep.call_args_list)

    def test_persistent_503_stops_after_four_attempts(self):
        opener = Mock(side_effect=HTTPError("https://example.amocrm.ru", 503, "unavailable", {}, None))
        with self.assertRaises(AmoAPIError):
            self.client(opener).get_all("leads")
        self.assertEqual(opener.call_count, 4)

    def test_missing_collection_does_not_become_empty_tasks(self):
        with self.assertRaises(AmoAPIError):
            self.client(Mock(return_value=Response({"_links": {}}))).get_all("tasks")

    def test_partial_export_is_not_returned_after_failure(self):
        opener = Mock(side_effect=[
            Response(collection("leads", [lead(1)], more=True)),
            HTTPError("https://example.amocrm.ru", 403, "forbidden", {}, None),
        ])
        with self.assertRaises(AmoAPIError):
            self.client(opener).get_all("leads")

    def test_write_endpoint_is_rejected_without_network(self):
        opener = Mock()
        with self.assertRaises(ValueError):
            self.client(opener).get_all("calls")
        opener.assert_not_called()


class CLITests(unittest.TestCase):
    def test_demo_runs_without_token_and_finds_expected_deals(self):
        env = os.environ.copy()
        env.pop("AMO_ACCESS_TOKEN", None)
        env.pop("AMO_SUBDOMAIN", None)
        result = subprocess.run([sys.executable, "-m", "citycar_case", "--demo"],
                                cwd=ROOT, env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data["data_source"], "synthetic_demo")
        self.assertEqual([row["lead_id"] for row in data["problem_deals"]],
                         [1001, 1002, 1006, 1007])

    def test_excerpt_fits_assignment_and_handles_two_collections(self):
        path = ROOT / "examples" / "amo_excerpt.py"
        self.assertLessEqual(len(path.read_text().splitlines()), 50)
        responses = [
            Response(collection("leads", [lead(1), lead(2)])),
            Response(collection("tasks", [task(22, 2, 900)])),
        ]
        output = io.StringIO()
        with patch.dict(os.environ, {"AMO_SUBDOMAIN": "example", "AMO_ACCESS_TOKEN": "fake-test-token"}), \
             patch("urllib.request.urlopen", side_effect=responses), \
             patch("time.time", return_value=1000), patch("time.sleep"), redirect_stdout(output):
            runpy.run_path(str(path))
        self.assertEqual(json.loads(output.getvalue()), [
            {"lead_id": 1, "no_open_tasks": True, "overdue_task_ids": []},
            {"lead_id": 2, "no_open_tasks": False, "overdue_task_ids": [22]},
        ])


if __name__ == "__main__":
    unittest.main()
