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

    # account create/enable spawn a per-account loop on the app loop: stub
    # its body so no test syncs a provider in the background (tests that
    # want the real loop re-enable it explicitly)
    async def _no_sync_loop(account_id):
        return None
    monkeypatch.setattr(sync, "_sync_loop", _no_sync_loop)
    # hetzner's module-level price cache would leak one test's catalog into
    # the next (order-dependent results) - every test starts cold
    from server.adapters import hetzner
    monkeypatch.setattr(hetzner, "_catalog", None)
    # same for the IP-cost cache: account ids restart in every test's DB
    from server import ips
    monkeypatch.setattr(ips, "_cost_cache", {})
    # fixture panel hosts (*.example.test) resolve to a documentation
    # address - never real DNS; tests of the internal-address guard
    # override this
    from server.adapters import gcore_hosting

    async def _resolve(host):
        return ["203.0.113.250"]
    monkeypatch.setattr(gcore_hosting, "resolve", _resolve)
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
        if path.endswith("/regions"):
            return httpx.Response(200, json={"regions": [{"name": "eu-west-3"}]})
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


def mock_gcore_transport():
    """Gcore MockTransport: projects/regions/instances/pricing plus the
    action->task->status state machine. Stateful like leaseweb's: an action
    POST returns {"tasks":[...]}; the task GET flips RUNNING->FINISHED on its
    second poll; the instance status flips only after the task FINISHED;
    DELETE removes the instance (later GETs 404); PATCH name/tags update the
    stored instance. Returns (transport, calls, headers) - headers captures
    Authorization per call for the APIKey assertion."""
    import re

    import httpx

    calls: list[str] = []
    headers: list[dict] = []

    task_state = {"state": "NEW", "polls": 0}
    status_override: dict[str, str] = {}
    deleted: set[str] = set()
    patches: dict[str, dict] = {}  # iid -> {"name": ..., "tags": [...]}

    # (project, region) -> [instance fixtures]
    layout = {
        ("101", "7"): ["web", "db"],
        ("102", "12"): ["edge"],
    }
    instances = {
        "web": fixture("gcore/instance_web.json"),
        "db": fixture("gcore/instance_db.json"),
        "edge": fixture("gcore/instance_edge.json"),
    }

    def _row(iid: str) -> dict | None:
        for row in instances.values():
            if row["id"] == iid:
                row = dict(row)
                if iid in patches:
                    row.update(patches[iid])
                if iid in status_override:
                    row["status"] = status_override[iid]
                return row
        return None

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        calls.append(path)
        headers.append(dict(request.headers))
        if path == "/cloud/v1/projects":
            return httpx.Response(200, json=fixture("gcore/projects.json"))
        if path == "/cloud/v1/regions":
            return httpx.Response(200, json=fixture("gcore/regions.json"))
        # instance list: /cloud/v1/instances/{p}/{r}
        m = re.match(r"^/cloud/v1/instances/([^/]+)/([^/]+)$", path)
        if m and request.method == "GET":
            rows = [dict(instances[n]) for n in layout.get((m.group(1), m.group(2)), [])]
            for r in rows:
                iid = r["id"]
                if iid in deleted:
                    continue
                if iid in patches:
                    r.update(patches[iid])
                if iid in status_override:
                    r["status"] = status_override[iid]
            return httpx.Response(200, json={"count": len(rows), "results": rows})
        # pricing: /cloud/v1/pricing/{p}/{r}/instances/{id}
        m = re.match(
            r"^/cloud/v1/pricing/([^/]+)/([^/]+)/instances/([^/]+)$", path)
        if m and request.method == "GET":
            if m.group(3) == instances["web"]["id"]:
                return httpx.Response(200, json=fixture("gcore/pricing_web.json"))
            if m.group(3) == instances["edge"]["id"]:
                return httpx.Response(200, json=fixture("gcore/pricing_edge.json"))
            return httpx.Response(404, json={"message": "no pricing"})
        # action: /cloud/v2/instances/{p}/{r}/{id}/action
        m = re.match(
            r"^/cloud/v2/instances/([^/]+)/([^/]+)/([^/]+)/action$", path)
        if m and request.method == "POST":
            body = json.loads(request.content)
            # status flips now (like leaseweb's mock); the adapter's poll
            # still has to observe it on a fresh GET
            status_override[m.group(3)] = \
                "SHUTOFF" if body["action"] == "stop" else "ACTIVE"
            task_state["state"] = "RUNNING"
            task_state["polls"] = 0
            return httpx.Response(200, json=fixture("gcore/tasks_started.json"))
        # task poll: /cloud/v1/tasks/{id}
        m = re.match(r"^/cloud/v1/tasks/([^/]+)$", path)
        if m and request.method == "GET":
            task_state["polls"] += 1
            if task_state["polls"] >= 2:
                task_state["state"] = "FINISHED"
            return httpx.Response(200, json={
                "id": m.group(1), "state": task_state["state"],
                "created_resources": {"instances": []},
                "error": None,
            })
        # instance detail: /cloud/v1/instances/{p}/{r}/{id}
        m = re.match(
            r"^/cloud/v1/instances/([^/]+)/([^/]+)/([^/]+)$", path)
        if m:
            iid = m.group(3)
            if request.method == "DELETE":
                deleted.add(iid)
                task_state["state"] = "RUNNING"
                task_state["polls"] = 0
                return httpx.Response(200, json=fixture("gcore/tasks_started.json"))
            if request.method == "PATCH":
                body = json.loads(request.content)
                patches.setdefault(iid, {})
                if "name" in body:
                    patches[iid]["name"] = body["name"]
                if "tags" in body:
                    # RFC 7386 merge patch (spec UpdateTagsSerializer):
                    # key:value adds/updates, null removes the key, "tags":
                    # null clears all user tags; unspecified keys and
                    # read-only tags are always preserved.
                    if body["tags"] is None:
                        body["tags"] = {}
                    base_row = _row(iid)
                    current = ({t["key"]: t for t in base_row["tags"]}
                               if base_row else {})
                    merged = {k: dict(t) for k, t in current.items()}
                    for k, v in body["tags"].items():
                        if v is None:
                            merged.pop(k, None)
                        else:
                            merged[k] = {"key": k, "value": v,
                                         "read_only": False}
                    patches[iid]["tags"] = list(merged.values())
                row = _row(iid)
                if row is None:
                    return httpx.Response(404, json={"message": "no instance"})
                return httpx.Response(200, json=row)
            row = _row(iid)
            if row is None or iid in deleted:
                return httpx.Response(404, json={"message": "no such instance"})
            return httpx.Response(200, json=row)
        return httpx.Response(404, json={"message": f"no fixture for {path}"})

    return httpx.MockTransport(handler), calls, headers


def mock_gcore_hosting_transport(*, expire_session_once: bool = False,
                                 subaccount_denied: bool = False,
                                 ip_appears_after: int = 0,
                                 payment_required: bool = False,
                                 live: bool = False):
    """Stateful BILLmanager double: every request is a form/query with func=.
    IP orders append a fresh documentation-range address (after
    `ip_appears_after` further service.ip reads, like a provisioning delay);
    deletes remove it. `live` swaps in the field shapes hosting.gcore.com
    actually sends (2026-10): item_status as {"$orig", "$": word}, a
    daily-billed server, gateway/mask on IPs. Returns (transport, calls,
    state) - calls are (http_method, func, params) with the password redacted."""
    import copy
    from urllib.parse import parse_qs

    import httpx

    sids = ("6201", "6202") if live else ("5101", "5102")
    state = {
        "ips": {sid: copy.deepcopy(fixture(f"gcore_hosting/service_ip_{sid}.json"))
                for sid in sids},
        "vds": copy.deepcopy(fixture("gcore_hosting/vds_live.json" if live
                                     else "gcore_hosting/vds.json")),
        "next_ip": 30, "pending": [], "expired": expire_session_once,
        "passwords": {},
    }
    calls: list[tuple[str, str, dict]] = []

    def _elems(doc):
        e = doc["doc"].get("elem", [])
        return e if isinstance(e, list) else [e]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            p = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        else:
            p = dict(request.url.params)
        func = p.get("func", "")
        logged = {k: ("<redacted>" if k in ("password", "passwd", "confirm") else v)
                  for k, v in p.items()}
        calls.append((request.method, func, logged))
        assert "password" not in request.url.params, "password must never be in a URL"
        if func == "auth":
            if p.get("password") != "panel-pw-0000":
                return httpx.Response(200, json={"doc": {"error": {
                    "$type": "auth", "msg": {"$": "Invalid username or password"}}}})
            return httpx.Response(200, json={"doc": {"auth": {"$": "sess-1"}}})
        if p.get("auth") != "sess-1":
            return httpx.Response(200, json=fixture("gcore_hosting/error_auth.json"))
        if state["expired"]:
            state["expired"] = False
            return httpx.Response(200, json=fixture("gcore_hosting/error_auth.json"))
        if func == "vds":
            return httpx.Response(200, json=state["vds"])
        if func == "service.ip":
            sid = p["elid"]
            doc = state["ips"][sid]
            for item in list(state["pending"]):
                if item["sid"] == sid:
                    if item["wait"] <= 0:
                        rows = _elems(doc)
                        rows.append(item["row"])
                        doc["doc"]["elem"] = rows
                        state["pending"].remove(item)
                    else:
                        item["wait"] -= 1
            return httpx.Response(200, json=doc)
        if func == "service.ip.edit":
            if p.get("sok") != "ok":
                return httpx.Response(200, json=fixture("gcore_hosting/ip_form.json"))
            if payment_required:
                return httpx.Response(200, json={"doc": {"billorder": {"$": "BO-77"},
                                                         "ok": {"$": "https://pay.example.test/77"}}})
            n = state["next_ip"]
            state["next_ip"] += 1
            row = {"id": {"$": f"95{n}"}, "name": {"$": f"203.0.113.{n}"},
                   "type": {"$": "Public IPv4"}}
            state["pending"].append({"sid": p["plid"], "row": row, "wait": ip_appears_after})
            return httpx.Response(200, json={"doc": {"ok": {"$": ""}}})
        if func == "service.ip.delete":
            doc = state["ips"][p["plid"]]
            rows = [r for r in _elems(doc) if r["id"]["$"] != p["elid"]]
            if len(rows) == len(_elems(doc)):
                return httpx.Response(200, json={"doc": {"error": {
                    "$type": "missed", "msg": {"$": "The item does not exist"}}}})
            doc["doc"]["elem"] = rows
            return httpx.Response(200, json={"doc": {"ok": {"$": ""}}})
        if func == "service.changepassword":
            state["passwords"][p["elid"]] = p["passwd"]
            return httpx.Response(200, json={"doc": {"ok": {"$": ""}}})
        if func == "vds.delete":
            state["vds"]["doc"]["elem"] = [r for r in _elems(state["vds"])
                                           if r["id"]["$"] != p["elid"]]
            return httpx.Response(200, json={"doc": {"ok": {"$": ""}}})
        if func == "payment":
            return httpx.Response(200, json=fixture("gcore_hosting/payment.json"))
        if func == "expense":
            return httpx.Response(200, json=fixture("gcore_hosting/expense.json"))
        if func == "subaccount":
            if subaccount_denied:
                return httpx.Response(200, json=fixture("gcore_hosting/error_access.json"))
            return httpx.Response(200, json=fixture("gcore_hosting/subaccount.json"))
        return httpx.Response(200, json={"doc": {"error": {
            "$type": "missed", "msg": {"$": f"unknown func {func}"}}}})

    return httpx.MockTransport(handler), calls, state


GCORE_HOSTING_CRED = json.dumps({"url": "https://panel.example.test/billmgr",
                                 "username": "ops@example.test",
                                 "password": "panel-pw-0000"})


class FakeAdapter:
    """Test double implementing the read + action surface for the API smoke
    test. Not a shipped abstraction - lives only in conftest."""
    key = "fake"
    display_name = "Fake"
    from server.adapters.base import TOKEN_FIELD, Capability
    capabilities = frozenset({Capability.REBOOT, Capability.RENAME, Capability.DELETE})
    credential_fields = (TOKEN_FIELD,)

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
