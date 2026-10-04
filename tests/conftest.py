import asyncio
import json
import os
import tempfile
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parent.parent / "server" / "fixtures"
TEST_TOKEN = "fixture-token-0000000000000000000000000000abcd"  # never a real secret


@pytest.fixture(autouse=True)
def test_env(monkeypatch):
    """Temp DB + test master key per test. Background loops (fleet sync,
    catalog sync) are disabled by default: they would make LIVE provider
    HTTP calls on every TestClient startup. Tests that want them re-enable
    explicitly."""
    tmp = tempfile.mkdtemp(prefix="omni-test-")
    monkeypatch.setenv("OMNICLOUD_DB", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("OMNICLOUD_MASTER_KEY", _fernet_key())
    monkeypatch.setenv("OMNICLOUD_COOKIE_SECURE", "0")  # TestClient speaks plain HTTP
    # config reads env at import; force re-read
    from server import config
    monkeypatch.setattr(config, "DB_PATH", Path(os.path.join(tmp, "test.db")))
    monkeypatch.setattr(config, "MASTER_KEY", _fernet_key())
    from server import db
    db.init()

    from server import catalog, sync

    # neuter the sync loops (live provider HTTP) but keep sync._loop wired to
    # the app loop, so order execution tests the PRODUCTION executor branch
    # (call_soon_threadsafe) instead of only the no-loop fallback thread
    def _start_all_no_tasks():
        sync._loop = asyncio.get_running_loop()
    monkeypatch.setattr(sync, "start_all", _start_all_no_tasks)
    monkeypatch.setattr(catalog, "start", lambda: None)
    yield
    sync._loop = None


def _fernet_key() -> str:
    from cryptography.fernet import Fernet
    return Fernet.generate_key().decode()


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def mock_hetzner_transport(responses: dict[str, list] | None = None):
    """MockTransport replaying fixtures by path. `responses` overrides paths,
    e.g. {"/servers": [429, 429, fixture("hetzner/servers_p1.json")]} - entries
    are either status ints (sent as bare 429) or dicts/JSON content."""
    import httpx

    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if "/servers/" in path and path.endswith("/actions/poweron") \
                and request.method == "POST":
            calls.append(path)
            # Verified: action POSTs return 201 with {action}
            return httpx.Response(201, json=fixture("hetzner/action_poweron.json"))
        if "/actions/" in path and request.method == "GET":
            calls.append(path)
            # Verified: terminal status is success, not finished
            return httpx.Response(200, json=fixture("hetzner/action_poweron_finished.json"))
        if path.endswith("/servers"):
            calls.append("/servers")
            if responses and "/servers" in responses:
                seq = responses["/servers"]
                idx = calls.count("/servers") - 1
                entry = seq[min(idx, len(seq) - 1)]
                if isinstance(entry, int):
                    return httpx.Response(entry)
                return httpx.Response(200, json=entry)
            return httpx.Response(200, json=fixture("hetzner/servers_p1.json"))
        if path.endswith("/server_types"):
            calls.append(path)
            return httpx.Response(200, json=fixture("hetzner/server_types_p1.json"))
        if path.endswith("/firewalls") and request.method == "GET":
            calls.append(path)
            return httpx.Response(200, json=fixture("hetzner/firewalls_p1.json"))
        if "/firewalls/" in path and "/actions/" in path and request.method == "POST":
            calls.append(path)
            # Verified: firewall actions return {actions: [...]}, 201
            return httpx.Response(201, json=fixture("hetzner/firewall_action.json"))
        return httpx.Response(404, json={"error": {"message": f"no fixture for {path}"}})

    return httpx.MockTransport(handler), calls


def mock_leaseweb_transport():
    import httpx

    calls: list[str] = []

    # stateful: power POSTs flip the instance state so polling can observe it
    state_override: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        calls.append(path)
        if path.endswith("/instances"):
            return httpx.Response(200, json=fixture("leaseweb/instances_p1.json"))
        if "/metrics/datatraffic" in path:
            # first instance has data, second has empty values
            if "4444" in path:
                return httpx.Response(200, json=fixture("leaseweb/metrics_empty.json"))
            return httpx.Response(200, json=fixture("leaseweb/metrics_a7f3.json"))
        if path.endswith("/instanceTypes"):
            return httpx.Response(200, json={"instanceTypes": [
                {"name": "lsw.m3.medium", "prices": {"hourly": "0.02", "monthly": "14.90"}},
                {"name": "lsw.m3.small", "prices": {"hourly": "0.01", "monthly": "8.40"}},
            ]})
        if "/instances/" in path and request.method == "GET":
            data = fixture("leaseweb/instances_p1.json")["instances"]
            row = dict(next((i for i in data if i["id"] in path), data[0]))
            sid = row["id"]
            if sid in state_override:
                row["state"] = state_override[sid]
            return httpx.Response(200, json=row)
        if "/instances/" in path and request.method == "POST":
            # power verbs flip state: start/reboot -> RUNNING, stop -> STOPPED
            sid = path.split("/instances/")[1].split("/")[0]
            verb = path.rsplit("/", 1)[-1]
            state_override[sid] = "STOPPED" if verb == "stop" else "RUNNING"
            return httpx.Response(202, json={})
        if "/instances/" in path and request.method in ("PUT", "DELETE"):
            return httpx.Response(200 if request.method == "PUT" else 204, json={})
        return httpx.Response(404, json={"correlationId": "x", "message": f"no fixture for {path}"})

    return httpx.MockTransport(handler), calls


def mock_ovh_transport(bearer_only=False):
    """OVH MockTransport serving both auth endpoints and both product trees.
    Stateful like leaseweb's: VPS power POSTs set a task state the next task
    GET returns; cloud power POSTs flip the instance status; cloud DELETE
    makes the instance GET 404. Returns (transport, calls, headers) -
    headers captures X-Ovh-*/Authorization per call for the auth assertions.
    bearer_only=True serves only the auth endpoints (auth-specific tests)."""
    import re

    import httpx

    calls: list[str] = []
    headers: list[dict] = []

    vps_task_state = {"state": "todo"}
    cloud_status: dict[str, str] = {}
    cloud_deleted: set[str] = set()
    vps_renamed: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        # the API base URL is https://eu.api.ovh.com/1.0 - strip the /1.0
        # prefix so the fixtures below check plain paths
        path = request.url.path.removeprefix("/1.0")
        calls.append(url)
        headers.append(dict(request.headers))
        # -- auth endpoints (never signed/Bearer'd by the adapter) ----------
        if url == "https://www.ovh.com/auth/oauth2/token":
            return httpx.Response(200, json=fixture("ovh/oauth_token.json"))
        if path == "/auth/time":
            return httpx.Response(200, json=fixture("ovh/auth_time.json"))
        if bearer_only:
            return httpx.Response(404)
        # -- VPS -----------------------------------------------------------
        if path == "/vps":
            return httpx.Response(200, json=fixture("ovh/vps_list.json"))
        m = re.match(r"^/vps/([^/]+)/tasks/(\d+)$", path)
        if m:
            # a pending task flips to done on its SECOND poll so tests can
            # observe the todo -> done transition (or never, for timeout)
            if vps_task_state["state"] == "todo":
                vps_task_state["polls"] = vps_task_state.get("polls", 0) + 1
                if vps_task_state["polls"] >= 2:
                    vps_task_state["state"] = "done"
            return httpx.Response(200, json={"id": int(m.group(2)),
                                             "state": vps_task_state["state"],
                                             "type": "rebootVm", "progress": 0,
                                             "date": "2026-10-04T12:00:00+02:00"})
        if path.endswith("/tasks"):
            return httpx.Response(200, json=[900001])
        if "/vps/" in path and request.method == "POST":
            verb = path.rsplit("/", 1)[-1]
            # reboot: todo on first POST so polling observes the transition;
            # stop: error (task failure path); start: done immediately
            vps_task_state["state"] = {"reboot": "todo", "stop": "error",
                                       "start": "done"}[verb]
            task = dict(fixture("ovh/vps_task.json"))
            task["state"] = vps_task_state["state"]
            task["type"] = {"reboot": "rebootVm", "stop": "stopVm",
                            "start": "startVm"}[verb]
            return httpx.Response(200, json=task)
        if re.match(r"^/vps/[^/]+/ips/[^/]+$", path) and request.method == "GET":
            return httpx.Response(200, json=fixture("ovh/vps_ip_detail.json"))
        if "/serviceInfos" in path:
            return httpx.Response(200, json=fixture("ovh/vps_service_infos.json"))
        if path.endswith("/ips"):
            return httpx.Response(200, json=fixture("ovh/vps_ips.json"))
        m = re.match(r"^/vps/([^/]+)$", path)
        if m:
            sn = m.group(1)
            if request.method == "PUT":
                vps_renamed[sn] = json.loads(request.content)["displayName"]
            row = dict(fixture("ovh/vps_service.json"))
            row["name"] = sn
            if sn in vps_renamed:
                row["displayName"] = vps_renamed[sn]
            return httpx.Response(200, json=row)
        # -- Public Cloud ---------------------------------------------------
        if path == "/cloud/project":
            return httpx.Response(200, json=fixture("ovh/cloud_projects.json"))
        if re.match(r"^/cloud/project/[^/]+/region/[^/]+/instance$", path):
            return httpx.Response(200, json=fixture("ovh/cloud_region_instances.json"))
        m = re.match(r"^/cloud/project/([^/]+)/instance/([0-9a-fA-F-]+)$", path)
        if m:
            project, iid = m.group(1), m.group(2)
            if request.method == "DELETE":
                cloud_deleted.add(iid)
                return httpx.Response(200, json={})
            if request.method == "PUT":
                return httpx.Response(200, json={})
            if iid in cloud_deleted:
                return httpx.Response(404, json={"message": "no such instance"})
            rows = fixture("ovh/cloud_instances.json")
            row = next((r for r in rows if r["id"] == iid), None)
            if row is None:
                return httpx.Response(404, json={"message": "no such instance"})
            row = dict(row)
            if iid in cloud_status:
                row["status"] = cloud_status[iid]
            return httpx.Response(200, json=row)
        if "/instance/" in path and request.method == "POST":
            verb = path.rsplit("/", 1)[-1]
            iid = path.split("/instance/")[1].split("/")[0]
            cloud_status[iid] = "SHUTOFF" if verb == "stop" else "ACTIVE"
            return httpx.Response(200, json={})
        if re.match(r"^/cloud/project/[^/]+/instance$", path):
            return httpx.Response(200, json=fixture("ovh/cloud_instances.json"))
        return httpx.Response(404, json={"message": f"no fixture for {path}"})

    return httpx.MockTransport(handler), calls, headers


class FakeAdapter:
    """Test double implementing the read + action surface for the API smoke
    test. Not a shipped abstraction - lives only in conftest."""
    key = "fake"
    display_name = "Fake"
    from server.adapters.base import Capability
    capabilities = frozenset({Capability.REBOOT, Capability.RENAME, Capability.DELETE})

    def __init__(self, account_id, account_name, token, http=None):
        self.account_id = account_id

    async def list_servers(self):
        from server.adapters.base import Server, ServerStatus
        return [
            Server(provider_id="fake-1", name="srv-fake-01", adapter="fake",
                   account_id=self.account_id, status=ServerStatus.RUNNING,
                   ipv4="203.0.113.99", region="test-1"),
        ]

    async def get_server(self, provider_id):
        return (await self.list_servers())[0]

    async def perform_action(self, cap, server_id, params):
        from server.adapters.base import ActionResult, AdapterError
        if server_id not in ("fake-1",):
            raise AdapterError(f"no such server: {server_id}")  # truthful: unknown ids fail
        return ActionResult(detail="fake done")

    async def close(self):
        pass


@pytest.fixture
def uid():
    """An admin user id, for tests that mutate as an admin."""
    from server import auth
    return auth.create_user("ordop", "pw123456", role="admin")
