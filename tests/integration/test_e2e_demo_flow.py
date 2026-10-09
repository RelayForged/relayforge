import asyncio
import json
import pytest
import httpx

from demo.run_demo import DemoRunner


class MockTransport(httpx.AsyncBaseTransport):
    def __init__(self):
        self.endpoints = {}
        self.subscriptions = {}
        self.events = []
        self.simulator_config = {"mode": "http_error", "rate": 0.0, "latency_ms": 0}
        self.restore_calls = []
        self.events_at_restore = None

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)

        # Health checks: /docs
        if url.endswith("/docs"):
            return httpx.Response(200, text="docs ok")

        # Dashboard endpoints: /api/chart/...
        if "/api/chart/endpoints" in url and not any(k in url for k in ["/state", "/eri-history", "/policy", "/explainability"]):
            return httpx.Response(200, json=list(self.endpoints.values()))

        if "/api/chart/endpoints/" in url and url.endswith("/state"):
            ep_id = url.split("/endpoints/")[1].split("/state")[0]
            # State logic based on phase/simulator/restore
            if ep_id in self.restore_calls:
                if self.events_at_restore is not None and len(self.events) > self.events_at_restore:
                    state = "AVAILABLE"
                else:
                    state = "RECOVERING"
            elif self.simulator_config["rate"] > 0.0:
                state = "QUARANTINED"
            else:
                state = "AVAILABLE"
            return httpx.Response(200, json={"endpoint_id": ep_id, "state": state})

        if "/api/chart/endpoints/" in url and "/eri-history" in url:
            return httpx.Response(200, json={"points": [{"score": 100.0 if self.simulator_config["rate"] == 0 else 35.0}]})

        if "/api/chart/endpoints/" in url and url.endswith("/policy"):
            return httpx.Response(200, json={"policy_id": "pol-demo-1", "strategy": "STRATEGY_C"})

        if "/api/chart/endpoints/" in url and "/explainability" in url:
            return httpx.Response(200, json={"records": [{"state_after": "QUARANTINED", "calculated_eri": 35.0, "reason": "Circuit breaker"}]})

        # Endpoint registration: POST /endpoints
        if "/endpoints" in url and not url.endswith("/restore") and request.method == "POST":
            body = json.loads(request.content.decode("utf-8"))
            ep_id = f"ep-{len(self.endpoints) + 1}"
            data = {"endpoint_id": ep_id, "target_url": body["target_url"], "owner_id": body.get("owner_id", "dev")}
            self.endpoints[ep_id] = data
            return httpx.Response(201, json=data)

        # Subscription creation: POST /subscriptions
        if "/subscriptions" in url and request.method == "POST":
            body = json.loads(request.content.decode("utf-8"))
            sub_id = f"sub-{len(self.subscriptions) + 1}"
            data = {"subscription_id": sub_id, "endpoint_id": body["endpoint_id"], "event_types": body["event_types"]}
            self.subscriptions[sub_id] = data
            return httpx.Response(201, json=data)

        # Simulator config: POST /simulator/config
        if "/simulator/config" in url and request.method == "POST":
            body = json.loads(request.content.decode("utf-8"))
            self.simulator_config.update(body)
            return httpx.Response(200, json=self.simulator_config)

        # Event ingestion: POST /events
        if "/events" in url and not url.endswith("/restore") and request.method == "POST":
            body = json.loads(request.content.decode("utf-8"))
            ev_id = f"ev-{len(self.events) + 1}"
            self.events.append(body)
            return httpx.Response(202, json={"event_id": ev_id})

        # Endpoint restore: POST /endpoints/{id}/restore
        if url.endswith("/restore") and request.method == "POST":
            ep_id = url.split("/endpoints/")[1].split("/restore")[0]
            self.restore_calls.append(ep_id)
            self.events_at_restore = len(self.events)
            return httpx.Response(200, json={"status": "RESTORE_REQUESTED", "endpoint_id": ep_id, "target_state": "RECOVERING"})

        return httpx.Response(404, text="Not Found")


@pytest.mark.asyncio
async def test_demo_runner_complete_flow():
    transport = MockTransport()
    client = httpx.AsyncClient(transport=transport)

    runner = DemoRunner(
        api_url="http://mock-api:8000",
        simulator_url="http://mock-sim:8080",
        dashboard_url="http://mock-dashboard:8090",
        target_url="http://simulator:8080/webhook",
        quick=True,
        nominal_count=3,
        fault_count=3,
        quarantine_count=3,
        recovery_count=3,
        client=client,
    )

    success = await runner.run()
    assert success is True

    # Assertions
    assert len(transport.endpoints) == 1
    assert len(transport.subscriptions) == 1
    assert runner.metrics["events_published"] == 170
    assert len(transport.restore_calls) == 1
    assert len(runner.metrics["state_transitions"]) >= 4

    await client.aclose()


@pytest.mark.asyncio
async def test_demo_runner_unhealthy_exit():
    # Transport that fails /docs health check
    class BrokenTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="Internal Server Error")

    client = httpx.AsyncClient(transport=BrokenTransport())
    runner = DemoRunner(
        api_url="http://broken:8000",
        simulator_url="http://broken:8080",
        quick=True,
        client=client,
    )

    success = await runner.run()
    assert success is False

    await client.aclose()
