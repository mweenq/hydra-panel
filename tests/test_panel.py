"""Сквозные тесты панели: авторизация, ноды, инбаунды, подписки, лимиты, бэкапы."""
from __future__ import annotations

import base64
import json
import tarfile
from datetime import datetime, timedelta, timezone

import pytest
import yaml
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from hydra import db
from hydra.services import backup, bottexts, clients as clients_svc, nodes as nodes_svc, subs, traffic
from hydra.services import devices, notify
from hydra.services import payments as pay_svc


def make_csr(cn: str = "node.local") -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, cn)]))
        .sign(key, hashes.SHA256())
    )
    return csr.public_bytes(serialization.Encoding.PEM).decode()


def new_client(admin, name: str, inbounds: list[int], days: int = 30, limit_gb: float = 0) -> tuple[int, str]:
    r = admin.post(
        "/users/new",
        data={"name": name, "days": str(days), "limit_gb": str(limit_gb), "note": "",
              "inbound_ids": [str(i) for i in inbounds]},
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text
    cid = int(r.headers["location"].split("/")[2].split("?")[0])
    return cid, db.q1("SELECT sub_token FROM clients WHERE id=?", (cid,))["sub_token"]


# ------------------------------------------------------------------ доступ
def test_root_redirects_to_dashboard(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code in (301, 302, 303)
    assert r.headers["location"] == "/dashboard"


def test_dashboard_requires_auth(client):
    r = client.get("/dashboard", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/login")


def test_login_rejects_wrong_password(client):
    r = client.post("/login", data={"username": "admin", "password": "wrong"}, follow_redirects=False)
    assert r.status_code == 401


def test_login_ok(admin):
    r = admin.get("/dashboard")
    assert r.status_code == 200
    assert "Обзор" in r.text


# ------------------------------------------------------------------ ноды
def test_node_created_with_token(admin, node):
    r = admin.get(f"/nodes/{node}?token=test-token-value")
    assert r.status_code == 200
    assert "install-node.sh" in r.text
    assert "test-token-value" in r.text


def test_join_rejects_unknown_token(client):
    r = client.post("/v1/join", json={"token": "x" * 24, "csr": make_csr(), "address": "1.2.3.4"})
    assert r.status_code == 403
    assert "invalid join token" in r.text


def test_join_issues_certificate(admin):
    r = admin.post(
        "/nodes/new",
        data={"name": "nl1", "address": "nl1.example.com", "engine": "xray", "api_port": "8443"},
        follow_redirects=False,
    )
    token = r.headers["location"].split("token=")[1]
    res = admin.post("/v1/join", json={"token": token, "csr": make_csr("nl1"), "address": "nl1.example.com"})
    assert res.status_code == 200, res.text
    data = res.json()
    assert "BEGIN CERTIFICATE" in data["cert_pem"]
    assert "BEGIN CERTIFICATE" in data["ca_pem"]
    row = db.q1("SELECT * FROM nodes WHERE id=?", (data["node_id"],))
    assert row["status"] == "online"
    assert row["join_token_hash"] == ""  # токен сгорел
    # повторное использование того же токена невозможно
    again = admin.post("/v1/join", json={"token": token, "csr": make_csr("nl1"), "address": "nl1.example.com"})
    assert again.status_code == 403


# ------------------------------------------------------------------ инбаунды
def test_inbound_reality_generates_keys(admin, node):
    r = admin.post(
        f"/nodes/{node}/connections",
        data={"protocol": "vless", "network": "raw", "security": "reality",
              "port": "443", "sni": "yahoo.com", "flow": "xtls-rprx-vision",
              "reality_dest": "yahoo.com:443", "remark": "Reality"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    row = db.q1("SELECT * FROM inbounds ORDER BY id DESC LIMIT 1")
    assert row["reality_private"] and row["reality_public"]
    assert len(row["reality_short_id"]) == 8
    assert row["security"] == "reality"


def test_inbound_tls_and_hy2(admin, node):
    for payload in (
        {"protocol": "vless", "network": "xhttp", "security": "tls", "port": "8444",
         "sni": "de1.example.com", "path": "/x", "remark": "XHTTP"},
        {"protocol": "trojan", "network": "grpc", "security": "tls", "port": "8445",
         "sni": "de1.example.com", "service_name": "tj", "remark": "Trojan"},
        {"protocol": "hysteria2", "security": "tls", "port": "8446", "sni": "de1.example.com",
         "obfs_type": "salamander", "obfs_password": "pw", "remark": "HY2"},
    ):
        r = admin.post(f"/nodes/{node}/connections", data=payload, follow_redirects=False)
        assert r.status_code == 303, r.text
    assert db.q1("SELECT COUNT(*) AS n FROM inbounds")["n"] >= 4


# ------------------------------------------------------------------ подписки
@pytest.fixture(scope="module")
def sub_client(admin):
    ids = [r["id"] for r in db.q("SELECT id FROM inbounds ORDER BY id")]
    return new_client(admin, "Абонент", ids, days=30, limit_gb=100)


def test_v2ray_links_are_valid(client, sub_client):
    _, token = sub_client
    r = client.get(f"/sub/{token}", headers={"User-Agent": "v2RayTun/3.1"})
    assert r.status_code == 200
    body = base64.b64decode(r.text).decode()
    lines = [ln for ln in body.splitlines() if ln]
    assert len(lines) == len(db.q("SELECT id FROM inbounds"))
    assert lines[0].startswith("vless://")
    assert any(ln.startswith("trojan://") for ln in lines)
    assert any(ln.startswith("hysteria2://") for ln in lines)
    reality = next(ln for ln in lines if "security=reality" in ln)
    assert "pbk=" in reality and "sid=" in reality and "flow=xtls-rprx-vision" in reality
    xhttp = next(ln for ln in lines if "type=xhttp" in ln)
    assert "path=%2Fx" in xhttp and "mode=auto" in xhttp


def test_client_detection(client, sub_client):
    _, token = sub_client
    cases = {
        "Happ/2.1": "application/json",
        "v2RayTun/3.1": "text/plain",
        "Shadowrocket/2.2": "text/plain",
        "clash-verge/1.7.7": "text/yaml",
        "mihomo/1.18.0": "text/yaml",
        "FlClashX/1.0": "text/yaml",
        "SFA/1.11.0": "application/json",
        "sing-box 1.10": "application/json",
    }
    for ua, ctype in cases.items():
        r = client.get(f"/sub/{token}", headers={"User-Agent": ua})
        assert r.status_code == 200, ua
        assert r.headers["content-type"].startswith(ctype), (ua, r.headers["content-type"])


def test_clash_profile_parses(client, sub_client):
    _, token = sub_client
    r = client.get(f"/sub/{token}", headers={"User-Agent": "mihomo/1.18"})
    doc = yaml.safe_load(r.text.split("\n", 1)[1])
    types = {p["type"] for p in doc["proxies"]}
    assert {"vless", "trojan", "hysteria2"} <= types
    assert doc["proxy-groups"][0]["type"] == "select"
    assert any(r["type"] == "url-test" for r in doc["proxy-groups"])
    assert doc["rules"] == ["MATCH,PROXY"]
    reality = next(p for p in doc["proxies"] if p["type"] == "vless" and p.get("reality-opts"))
    assert reality["servername"] == "yahoo.com"
    assert reality["flow"] == "xtls-rprx-vision"


def test_singbox_profile_parses(client, sub_client):
    _, token = sub_client
    r = client.get(f"/sub/{token}", headers={"User-Agent": "SFA/1.11"})
    doc = json.loads(r.text)
    assert doc["outbounds"][0]["type"] == "selector"
    assert any(o["type"] == "hysteria2" for o in doc["outbounds"])
    assert doc["route"]["final"] == "proxy"


def test_subscription_headers(client, sub_client):
    cid, token = sub_client
    r = client.get(f"/sub/{token}", headers={"User-Agent": "Happ/2"})
    info = r.headers["subscription-userinfo"]
    assert "upload=" in info and "download=" in info and "total=" in info and "expire=" in info
    assert r.headers["profile-update-interval"] == "12"
    c = clients_svc.get_client(cid)
    assert c["sub_token"] == token


def test_forced_format(client, sub_client):
    _, token = sub_client
    assert client.get(f"/sub/{token}/clash").headers["content-type"].startswith("text/yaml")
    assert client.get(f"/sub/{token}/singbox").headers["content-type"].startswith("application/json")
    assert client.get(f"/sub/{token}/links").text.startswith("vless://")


def test_browser_gets_html_page(client, sub_client):
    _, token = sub_client
    r = client.get(f"/sub/{token}", headers={"User-Agent": "Mozilla/5.0", "Accept": "text/html"})
    assert r.status_code == 200
    assert "Ссылка на подписку" in r.text
    assert "Happ" in r.text


# ------------------------------------------------------------------ трафик
def test_traffic_accounting_and_limit(admin):
    cid, token = new_client(admin, "Лимит", [1], days=30, limit_gb=1)
    uid = db.q1("SELECT uuid FROM clients WHERE id=?", (cid,))["uuid"]
    traffic.apply_stats(1, {f"{uid}|vless-raw-443": {"up": 600 * 1024**2, "down": 600 * 1024**2}})
    c = clients_svc.get_client(cid)
    assert c["used"] == 1200 * 1024**2
    disabled = traffic.enforce_limits()
    assert "Лимит" in disabled
    c = clients_svc.get_client(cid)
    assert c["enabled"] == 0
    assert "auto-off" in c["note"]
    assert admin.get(f"/sub/{token}", headers={"User-Agent": "Happ"}).status_code == 403


def test_expired_client_blocked(admin):
    cid, token = new_client(admin, "Просрочен", [1], days=0)
    db.ex("UPDATE clients SET expire_at='2020-01-01T00:00:00Z' WHERE id=?", (cid,))
    traffic.enforce_limits()
    assert admin.get(f"/sub/{token}", headers={"User-Agent": "Happ"}).status_code == 403


def test_extend_reenables(admin):
    cid, token = new_client(admin, "Продление", [1], days=0)
    db.ex("UPDATE clients SET expire_at='2020-01-01T00:00:00Z' WHERE id=?", (cid,))
    traffic.enforce_limits()
    assert admin.get(f"/sub/{token}", headers={"User-Agent": "Happ"}).status_code == 403
    exp = clients_svc.extend(cid, 30)
    assert exp > db.now()
    assert admin.get(f"/sub/{token}", headers={"User-Agent": "Happ"}).status_code == 200


# ------------------------------------------------------------------ конфиги
def test_xray_config_shape(node):
    payload = nodes_svc.build_payload(node)
    cfg = payload["xray"]
    assert cfg["api"]["tag"] == "api"
    assert "StatsService" in cfg["api"]["services"]
    assert cfg["policy"]["levels"]["0"]["statsUserUplink"] is True
    tags = [i["tag"] for i in cfg["inbounds"]]
    assert "api" in tags
    reality = next(i for i in cfg["inbounds"] if i["tag"].startswith("vless-raw"))
    assert reality["streamSettings"]["network"] == "raw"
    assert reality["streamSettings"]["realitySettings"]["dest"] == "yahoo.com:443"
    tls_inbound = next(i for i in cfg["inbounds"] if i["tag"].startswith("vless-xhttp"))
    assert tls_inbound["streamSettings"]["fallbacks"] == [{"dest": 8080, "xver": 0}]
    # email клиента — uuid|tag, по нему разбирается статистика
    email = reality["settings"]["clients"][0]["email"]
    assert email.count("|") == 1


def test_hysteria_config(node):
    payload = nodes_svc.build_payload(node)
    y = yaml.safe_load(payload["hysteria2"])
    assert y["listen"].startswith(":")
    assert y["auth"]["type"] == "userpass"
    assert y["trafficStats"]["secret"] == "hydra-stats"
    assert y["obfs"]["type"] == "salamander"


def test_payload_excludes_disabled(admin, node):
    cid, _ = new_client(admin, "Отключаемый", [1])
    nodes_svc.build_payload(node)
    clients_svc.update_client(cid, enabled=0)
    payload = nodes_svc.build_payload(node)
    emails = [
        c["email"]
        for i in payload["xray"]["inbounds"]
        for c in (i["settings"].get("clients") or [])
    ]
    uid = db.q1("SELECT uuid FROM clients WHERE id=?", (cid,))["uuid"]
    assert not any(e.startswith(uid) for e in emails)
    clients_svc.update_client(cid, enabled=1)


# ------------------------------------------------------------------ API
def test_api_key_auth(app):
    db.set_setting("api_key", "secret-key")
    with TestClient(app) as anon:
        assert anon.get("/api/stats").status_code == 401
        assert anon.get("/api/stats", headers={"X-Api-Key": "nope"}).status_code == 401
        assert anon.get("/api/stats", headers={"X-Api-Key": "secret-key"}).status_code == 200


# ------------------------------------------------------------------ бэкапы
def test_backup_and_restore(admin):
    before = db.q1("SELECT name FROM clients ORDER BY id LIMIT 1")["name"]
    path = backup.create()
    assert path.exists()
    with tarfile.open(path) as tar:
        names = tar.getnames()
    assert "hydra.db" in names and "settings.json" in names
    assert any(n.startswith("certs/") for n in names)
    db.ex("UPDATE clients SET name='ИСПОРЧЕНО' WHERE id=1")
    backup.restore(path)
    db.init_db()
    assert db.q1("SELECT name FROM clients WHERE id=1")["name"] == before


# ------------------------------------------------------------------ маскировка
def test_decoy_page(client):
    r = client.get("/decoy")
    assert r.status_code == 200
    assert "Infrastructure" in r.text


# ------------------------------------------------------------------ новые разделы
def test_dashboard_periods(admin):
    for period in ("day", "month", "year"):
        r = admin.get(f"/dashboard?period={period}")
        assert r.status_code == 200
        assert 'class="line"' in r.text
        assert "Трафик" in r.text


def test_users_page_marks_expiring(admin):
    cid, _ = new_client(admin, "Скоро истекает", [1], days=3)
    r = admin.get("/users")
    assert r.status_code == 200
    assert "осталось" in r.text or "дн." in r.text
    c = clients_svc.get_client(cid)
    assert clients_svc.days_left(c) in (2, 3)


def test_routing_rule_reaches_profiles(admin, sub_client):
    _, token = sub_client
    r = admin.post("/routing/new",
                   data={"kind": "suffix", "value": "example.org", "action": "direct"},
                   follow_redirects=False)
    assert r.status_code == 303
    page = admin.get("/routing")
    assert "example.org" in page.text
    clash = admin.get(f"/sub/{token}/clash").text
    assert "DOMAIN-SUFFIX,example.org,DIRECT" in clash
    sbox = json.loads(admin.get(f"/sub/{token}/singbox").text)
    assert any("example.org" in (r.get("domain_suffix") or []) for r in sbox["route"]["rules"])
    rid = db.q1("SELECT id FROM routes ORDER BY id DESC LIMIT 1")["id"]
    admin.post(f"/routing/{rid}/delete")
    assert "example.org" not in admin.get(f"/sub/{token}/clash").text


def test_routing_preset_and_block(admin, sub_client):
    _, token = sub_client
    admin.post("/routing/preset", data={"preset": "ads"})
    sbox = json.loads(admin.get(f"/sub/{token}/singbox").text)
    assert any(o["type"] == "block" for o in sbox["outbounds"])
    clash = admin.get(f"/sub/{token}/clash").text
    assert "REJECT" in clash


def test_app_format_override(admin, sub_client):
    _, token = sub_client
    before = admin.get(f"/sub/{token}", headers={"User-Agent": "mihomo/1.18"})
    assert before.headers["content-type"].startswith("text/yaml")
    admin.post("/subscription/apps", data={"fmt_mihomo": "singbox", "on_mihomo": "on"})
    after = admin.get(f"/sub/{token}", headers={"User-Agent": "mihomo/1.18"})
    assert after.headers["content-type"].startswith("application/json")
    admin.post("/subscription/apps", data={"fmt_mihomo": "clash", "on_mihomo": "on"})


def test_disabled_app_falls_back(admin, sub_client):
    _, token = sub_client
    admin.post("/subscription/apps", data={"fmt_happ": "v2ray"})  # без on_ → выключен
    r = admin.get(f"/sub/{token}", headers={"User-Agent": "Happ/2.1"})
    assert r.status_code == 200
    admin.post("/subscription/apps", data={"fmt_happ": "v2ray", "on_happ": "on"})


def test_subscription_preview(admin):
    admin.post("/subscription/apps", data={"fmt_happ": "happ", "on_happ": "on"})
    r = admin.get("/subscription", params={"ua": "Happ/2.1.0 (iPhone; iOS 17.4)"})
    assert r.status_code == 200
    assert "Happ" in r.text
    assert "профилей" in r.text


def test_sub_page_marks_device(client, sub_client):
    _, token = sub_client
    r = client.get(f"/sub/{token}",
                   headers={"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4)", "Accept": "text/html"})
    assert r.status_code == 200
    assert "ваше устройство" in r.text


def test_bots_page_and_save(admin):
    r = admin.get("/bots")
    assert r.status_code == 200
    r = admin.post("/bots/save", data={"bot_token": "", "user_bot_token": "",
                                       "admin_tg_ids": "42", "backup_tg_chat": ""},
                   follow_redirects=False)
    assert r.status_code == 303
    assert db.get_setting("admin_tg_ids") == "42"


def test_engines_crud(admin):
    r = admin.post("/settings/engines/new",
                   data={"name": "test-engine", "kind": "xray", "binary": "/usr/bin/x"},
                   follow_redirects=False)
    assert r.status_code == 303
    row = db.q1("SELECT * FROM engines WHERE name='test-engine'")
    assert row and row["kind"] == "xray"
    admin.post(f"/settings/engines/{row['id']}/update",
               data={"kind": "singbox", "binary": "/usr/bin/y", "enabled": "on"})
    assert db.q1("SELECT kind FROM engines WHERE id=?", (row["id"],))["kind"] == "singbox"
    admin.post(f"/settings/engines/{row['id']}/delete")
    assert db.q1("SELECT id FROM engines WHERE id=?", (row["id"],)) is None


def test_legacy_redirects(admin):
    assert admin.get("/clients", follow_redirects=False).status_code == 303
    assert admin.get("/inbounds", follow_redirects=False).status_code == 303


# ------------------------------------------------------------------ Happ JSON
def test_happ_json_profile(admin, sub_client):
    _, token = sub_client
    admin.post("/subscription/apps", data={"fmt_happ": "happ", "on_happ": "on"})
    r = admin.get(f"/sub/{token}", headers={"User-Agent": "Happ/2.1.0 (iPhone; iOS 17.4)"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    items = json.loads(r.text)
    assert isinstance(items, list)
    servers = db.q1("SELECT COUNT(*) AS n FROM inbounds WHERE enabled=1")["n"]
    assert len(items) == servers + 1          # конфиг на каждый сервер + «Автовыбор»

    auto = items[0]
    assert "Автовыбор" in auto["remarks"]
    assert auto["routing"]["balancers"][0]["tag"] == "auto"
    assert auto["routing"]["balancers"][0]["selector"] == ["proxy-"]
    assert auto["observatory"]["subjectSelector"] == ["proxy-"]
    assert [o["tag"] for o in auto["outbounds"]][-2:] == ["direct", "block"]

    single = items[1]
    assert single["remarks"].startswith(db.get_setting("sub_title"))
    assert [o["tag"] for o in single["outbounds"]] == ["proxy", "direct", "block"]
    assert {i["protocol"] for i in single["inbounds"]} == {"socks", "http"}
    assert single["routing"]["rules"]

    uuid = db.q1("SELECT uuid FROM clients WHERE sub_token=?", (token,))["uuid"]
    vless = next(o for o in single["outbounds"] if o["protocol"] == "vless")
    assert vless["settings"]["vnext"][0]["users"][0]["id"] == uuid
    # hysteria2 в диалекте Happ — protocol "hysteria" со своим транспортом
    hy = [o for it in items for o in it["outbounds"] if o["protocol"] == "hysteria"]
    assert hy and hy[0]["streamSettings"]["hysteriaSettings"]["auth"] == uuid


def test_happ_singbox_setting_is_ignored(admin, sub_client):
    """Старый сохранённый «sing-box JSON» для Happ не должен ломать импорт."""
    _, token = sub_client
    admin.post("/subscription/apps", data={"fmt_happ": "singbox", "on_happ": "on"})
    r = admin.get(f"/sub/{token}", headers={"User-Agent": "Happ/2.1"})
    doc = json.loads(r.text)
    assert all("protocol" in o for o in doc[0]["outbounds"])   # xray, не sing-box
    assert subs.apps_view() and next(a for a in subs.apps_view() if a["key"] == "happ")["format"] == "happ"
    admin.post("/subscription/apps", data={"fmt_happ": "happ", "on_happ": "on"})


def test_happ_forced_format(client, sub_client):
    _, token = sub_client
    r = client.get(f"/sub/{token}/happ")
    assert r.headers["content-type"].startswith("application/json")
    items = json.loads(r.text)
    assert isinstance(items, list) and items[0]["outbounds"]


def test_happ_routing_header(admin, sub_client):
    _, token = sub_client
    admin.post("/subscription/apps", data={"fmt_happ": "v2ray", "on_happ": "on"})
    r = admin.get(f"/sub/{token}", headers={"User-Agent": "Happ/2.1"})
    link = r.headers["routing"]
    assert link.startswith("happ://routing/onadd/")
    profile = json.loads(base64.b64decode(link.split("/")[-1]).decode())
    assert profile["DirectIp"] and profile["GlobalProxy"] == "true"
    # у не-Happ клиентов заголовка нет
    assert "routing" not in admin.get(f"/sub/{token}", headers={"User-Agent": "v2RayTun/3"}).headers
    admin.post("/subscription/apps", data={"fmt_happ": "happ", "on_happ": "on"})


# ------------------------------------------------------------------ оплата
def test_plans_and_payment_apply(admin, sub_client):
    cid, _ = sub_client
    r = admin.post("/bots/payments/plans/new",
                   data={"name": "Тест 30", "days": "30", "gb": "50", "price": "199",
                         "currency": "RUB", "note": ""},
                   follow_redirects=False)
    assert r.status_code == 303
    plan = next(p for p in pay_svc.list_plans() if p["name"] == "Тест 30")
    assert plan["price_h"].startswith("199")

    before = clients_svc.get_client(cid)
    payment_id = pay_svc.create_payment(before, plan, "manual", "777")
    paid = pay_svc.mark_paid(payment_id)
    assert paid["status"] == "paid"

    after = clients_svc.get_client(cid)
    assert after["limit_bytes"] == before["limit_bytes"] + 50 * 1024**3
    assert after["expire_at"] > before["expire_at"]
    # повторное подтверждение не начисляет второй раз
    pay_svc.mark_paid(payment_id)
    assert clients_svc.get_client(cid)["limit_bytes"] == after["limit_bytes"]


def test_payments_page_and_approve(admin, sub_client):
    cid, _ = sub_client
    plan = pay_svc.list_plans(only_enabled=True)[0]
    payment_id = pay_svc.create_payment(clients_svc.get_client(cid), plan, "manual", "777")
    assert admin.get("/bots/payments").status_code == 200
    r = admin.post(f"/bots/payments/{payment_id}/approve", follow_redirects=False)
    assert r.status_code == 303
    assert pay_svc.get_payment(payment_id)["status"] == "paid"


def test_payment_methods_depend_on_settings(admin):
    plan = pay_svc.list_plans()[0]
    db.set_setting("pay_manual_enabled", 0)
    db.set_setting("pay_manual_details", "")
    assert pay_svc.methods_for(plan) == []          # XTR → звёзды
    stars = dict(plan, currency="XTR")
    assert pay_svc.methods_for(stars) == ["stars"]
    db.set_setting("pay_provider_token", "123:TEST")
    assert pay_svc.methods_for(plan) == ["provider"]
    db.set_setting("pay_provider_token", "")


# ------------------------------------------------------------------ тексты бота
def test_bot_texts_editor(admin):
    r = admin.get("/bots/texts")
    assert r.status_code == 200
    assert "Сообщения бота" in r.text
    admin.post("/bots/texts/save", data={"t_status": "<b>Привет, {name}</b>"})
    assert bottexts.get("status") == "<b>Привет, {name}</b>"
    assert bottexts.render("status", name="Аня") == "<b>Привет, Аня</b>"
    # неизвестная переменная не роняет отправку
    assert bottexts.render("status", name="Аня").find("{") == -1
    admin.post("/bots/texts/reset", data={"key": "status"})
    assert not bottexts.is_custom("status")
    assert "{name}" in bottexts.get("status")


def test_bot_texts_unknown_var_survives(admin):
    admin.post("/bots/texts/save", data={"t_status": "hi {nope}"})
    assert bottexts.render("status") == "hi {nope}"
    admin.post("/bots/texts/reset", data={"all": "1"})
    assert bottexts.overrides() == {}


# ------------------------------------------------------------------ HWID
def test_hwid_devices_registered(admin, sub_client):
    cid, token = sub_client
    db.set_setting("hwid_limit", 0)
    admin.post(f"/users/{cid}/devices/reset")
    h = {"User-Agent": "Happ/2.1", "X-HWID": "dev-aaa", "X-Device-OS": "iOS",
         "X-Device-Model": "iPhone 15", "X-Ver-OS": "17.4"}
    r = admin.get(f"/sub/{token}", headers=h)
    assert r.status_code == 200
    assert r.headers["x-hwid-active"] == "true"
    rows = devices.list_devices(cid)
    assert len(rows) == 1
    assert rows[0]["model"] == "iPhone 15" and rows[0]["platform"] == "iOS"
    # повторный запрос тем же HWID не плодит записи
    admin.get(f"/sub/{token}", headers=h)
    assert devices.count(cid) == 1


def test_hwid_limit_blocks_new_device(admin, sub_client):
    cid, token = sub_client
    db.set_setting("hwid_limit", 1)
    admin.post(f"/users/{cid}/devices/reset")
    ok = admin.get(f"/sub/{token}", headers={"User-Agent": "Happ/2.1", "X-HWID": "dev-1"})
    assert ok.status_code == 200
    bad = admin.get(f"/sub/{token}", headers={"User-Agent": "Happ/2.1", "X-HWID": "dev-2"})
    assert bad.status_code == 403
    assert bad.headers["x-hwid-max-devices-reached"] == "true"
    # без HWID при включённом лимите — тоже отказ
    none = admin.get(f"/sub/{token}", headers={"User-Agent": "Happ/2.1"})
    assert none.status_code == 403
    assert none.headers["x-hwid-not-supported"] == "true"
    db.set_setting("hwid_limit", 0)


def test_hwid_unlink_from_panel(admin, sub_client):
    cid, token = sub_client
    db.set_setting("hwid_limit", 0)
    admin.post(f"/users/{cid}/devices/reset")
    admin.get(f"/sub/{token}", headers={"User-Agent": "Happ/2.1", "X-HWID": "dev-x"})
    row = devices.list_devices(cid)[0]
    r = admin.post(f"/users/{cid}/devices/{row['id']}/delete", follow_redirects=False)
    assert r.status_code == 303
    assert devices.count(cid) == 0


def test_hwid_payment_resets_devices(admin, sub_client):
    cid, token = sub_client
    db.set_setting("hwid_limit", 0)
    db.set_setting("hwid_reset_on_pay", 1)
    admin.get(f"/sub/{token}", headers={"User-Agent": "Happ/2.1", "X-HWID": "dev-pay"})
    assert devices.count(cid) == 1
    plan = pay_svc.list_plans(only_enabled=True)[0]
    pid = pay_svc.create_payment(clients_svc.get_client(cid), plan, "manual", "777")
    pay_svc.mark_paid(pid)
    assert devices.count(cid) == 0
    db.set_setting("hwid_reset_on_pay", 0)


def test_notify_due_clients(admin, sub_client):
    cid, _ = sub_client
    soon = (datetime.now(timezone.utc) + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    db.ex("UPDATE clients SET tg_id='999', expire_at=? WHERE id=?", (soon, cid))
    assert any(c["id"] == cid for c in notify.due_clients(3))
    assert not any(c["id"] == cid for c in notify.due_clients(1))
    # сухой прогон считает получателей, но ничего не отправляет
    assert notify.send_reminders(dry_run=True) >= 1
    db.ex("UPDATE clients SET tg_id='' WHERE id=?", (cid,))


def test_user_page_shows_devices(admin, sub_client):
    cid, token = sub_client
    admin.get(f"/sub/{token}", headers={"User-Agent": "Happ/2.1", "X-HWID": "dev-ui-check"})
    r = admin.get(f"/users/{cid}")
    assert r.status_code == 200
    assert "Устройства" in r.text
    assert "dev-ui-" in r.text                      # короткий HWID в таблице
    s = admin.get("/settings")
    assert "Устройства (HWID)" in s.text
    assert "Уведомления" in s.text


def test_client_appears_in_node_payload(admin, node):
    """Новый клиент должен попадать в конфиг ноды (иначе xray отвергнет его
    как invalid request user id)."""
    r = admin.post(
        f"/nodes/{node}/connections",
        data={"protocol": "vless", "network": "raw", "security": "reality",
              "port": "2455", "sni": "yahoo.com", "flow": "xtls-rprx-vision",
              "reality_dest": "yahoo.com:443", "remark": "t-push"},
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text
    inb = db.q1("SELECT id FROM inbounds WHERE tag='vless-raw-2455'")
    assert inb, "инбаунд не создался"
    cid, _ = new_client(admin, "t-client-push", [inb["id"]], 30, 0)
    uuid = clients_svc.get_client(cid)["uuid"]
    payload = nodes_svc.build_payload(node)
    found = any(
        any(c.get("id") == uuid for c in ib.get("settings", {}).get("clients", []))
        for ib in (payload.get("xray") or {}).get("inbounds", [])
    )
    assert found, "uuid клиента не попал в конфиг ноды"
    # после удаления клиента он должен исчезнуть из конфига
    clients_svc.delete_client(cid)
    payload2 = nodes_svc.build_payload(node)
    found2 = any(
        any(c.get("id") == uuid for c in ib.get("settings", {}).get("clients", []))
        for ib in (payload2.get("xray") or {}).get("inbounds", [])
    )
    assert not found2
