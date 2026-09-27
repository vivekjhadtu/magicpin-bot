"""Local smoke test — pushes the full dataset into the running bot and
exercises tick + reply scenarios. Not part of the submission; dev-only."""
import json
import sys
from pathlib import Path
import httpx

BASE = "http://localhost:8080"
DATASET = Path(__file__).parent / "dataset"


def push_all():
    with httpx.Client(timeout=10) as c:
        n = 0
        for f in (DATASET / "categories").glob("*.json"):
            data = json.load(open(f))
            r = c.post(f"{BASE}/v1/context", json={
                "scope": "category", "context_id": data["slug"], "version": 1,
                "payload": data, "delivered_at": "2026-04-26T10:00:00Z"})
            assert r.json()["accepted"], r.text
            n += 1
        for kind, id_field in [("merchants", "merchant_id"), ("customers", "customer_id"), ("triggers", "id")]:
            for f in (DATASET / kind).glob("*.json"):
                data = json.load(open(f))
                scope = {"merchants": "merchant", "customers": "customer", "triggers": "trigger"}[kind]
                r = c.post(f"{BASE}/v1/context", json={
                    "scope": scope, "context_id": data[id_field], "version": 1,
                    "payload": data, "delivered_at": "2026-04-26T10:00:00Z"})
                assert r.json()["accepted"], r.text
                n += 1
        print(f"pushed {n} contexts")


def test_idempotency():
    with httpx.Client(timeout=10) as c:
        payload = json.load(open(DATASET / "categories" / "dentists.json"))
        r1 = c.post(f"{BASE}/v1/context", json={"scope": "category", "context_id": "dentists", "version": 1, "payload": payload, "delivered_at": "x"})
        assert r1.json()["accepted"]
        r2 = c.post(f"{BASE}/v1/context", json={"scope": "category", "context_id": "dentists", "version": 1, "payload": payload, "delivered_at": "x"})
        assert r2.json()["accepted"]  # idempotent no-op
        r3 = c.post(f"{BASE}/v1/context", json={"scope": "category", "context_id": "dentists", "version": 0, "payload": payload, "delivered_at": "x"})
        assert r3.json()["accepted"] is False and r3.json()["reason"] == "stale_version"
        print("idempotency OK")


def test_tick_and_pairs():
    pairs = json.load(open(DATASET / "test_pairs.json"))["pairs"]
    trigger_ids = [p["trigger_id"] for p in pairs]
    with httpx.Client(timeout=30) as c:
        r = c.post(f"{BASE}/v1/tick", json={"now": "2026-04-26T10:30:00Z", "available_triggers": trigger_ids})
        data = r.json()
        actions = data["actions"]
        print(f"tick returned {len(actions)} actions for {len(trigger_ids)} test-pair triggers")
        for a in actions[:5]:
            print(" -", a["body"][:140])
        # first tick capped at MAX_ACTIONS_PER_TICK=20; remaining trickle in on next tick(s)
        r2 = c.post(f"{BASE}/v1/tick", json={"now": "2026-04-26T10:35:00Z", "available_triggers": trigger_ids})
        actions2 = r2.json()["actions"]
        print(f"second tick returned {len(actions2)} more actions (remainder past the 20-cap)")
        # now every trigger has fired once -> a third tick must be fully suppressed
        r3 = c.post(f"{BASE}/v1/tick", json={"now": "2026-04-26T10:40:00Z", "available_triggers": trigger_ids})
        assert len(r3.json()["actions"]) == 0, "expected full suppression dedup once every trigger has fired"
        print("suppression dedup OK")
        return actions


def test_reply_flows():
    with httpx.Client(timeout=15) as c:
        mid = "m_001_drmeera_dentist_delhi"

        # auto-reply hell
        auto_msg = "Thank you for contacting us! Our team will respond shortly."
        ended = False
        for i in range(1, 5):
            r = c.post(f"{BASE}/v1/reply", json={
                "conversation_id": f"conv_auto_{i}", "merchant_id": mid, "customer_id": None,
                "from_role": "merchant", "message": auto_msg, "received_at": "x", "turn_number": i + 1})
            d = r.json()
            print(f"auto-reply turn {i}: action={d['action']}")
            if d["action"] == "end":
                ended = True
                break
        assert ended, "bot never detected auto-reply pattern"
        print("auto-reply detection OK")

        # intent transition
        r = c.post(f"{BASE}/v1/reply", json={
            "conversation_id": "conv_intent_1", "merchant_id": mid, "customer_id": None,
            "from_role": "merchant", "message": "Ok lets do it. Whats next?", "received_at": "x", "turn_number": 2})
        d = r.json()
        print("intent transition:", d["action"], "-", d.get("body", ""))
        body_lower = d.get("body", "").lower()
        qualifying = ["would you", "do you", "can you tell", "what if", "how about"]
        actioning = ["done", "sending", "draft", "here", "confirm", "proceed", "next"]
        assert any(w in body_lower for w in actioning) and not any(w in body_lower for w in qualifying)
        print("intent transition OK")

        # hostile
        r = c.post(f"{BASE}/v1/reply", json={
            "conversation_id": "conv_hostile", "merchant_id": mid, "customer_id": None,
            "from_role": "merchant", "message": "Stop messaging me. This is useless spam.",
            "received_at": "x", "turn_number": 2})
        d = r.json()
        print("hostile:", d["action"])
        assert d["action"] == "end" or (d["action"] == "send" and any(w in d.get("body", "").lower() for w in ["sorry", "apolog", "won't"]))
        print("hostile handling OK")


if __name__ == "__main__":
    push_all()
    test_idempotency()
    actions = test_tick_and_pairs()
    test_reply_flows()
    print("\nALL SMOKE TESTS PASSED")
