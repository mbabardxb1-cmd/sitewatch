"""Domain (RDAP/WHOIS), hosting and SSL expiry: lookups, effective dates, alerts, API."""
import socketserver
import threading
from datetime import UTC, date, datetime, timedelta

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

from app.expiry import hosting, lookup
from app.expiry import service as svc
from app.models import DomainRecord, HostingIntegration, NotificationLog, NotificationRule, SslCertificate
from app.notify.senders import MemoryNotifier
from tests.auditsite import Site

TODAY = datetime.now(UTC).date()


def rdap_doc(domain, expires=None, registrar="Example Registrar, Inc."):
    events = [{"eventAction": "registration", "eventDate": "1995-08-14T04:00:00Z"},
              {"eventAction": "last changed", "eventDate": "2024-08-14T07:01:34Z"}]
    if expires:
        events.append({"eventAction": "expiration", "eventDate": expires})
    return {
        "objectClassName": "domain", "ldhName": domain.upper(), "rdapConformance": ["rdap_level_0"],
        "events": events,
        "entities": [{"objectClassName": "entity", "handle": "376", "roles": ["registrar"],
                      "vcardArray": ["vcard", [["version", {}, "text", "4.0"], ["fn", {}, "text", registrar]]],
                      "links": [{"rel": "about", "href": "https://registrar.example/"}]}],
        "nameservers": [{"objectClassName": "nameserver", "ldhName": "A.IANA-SERVERS.NET"},
                        {"objectClassName": "nameserver", "ldhName": "B.IANA-SERVERS.NET"}],
    }


class _Whois(socketserver.StreamRequestHandler):
    def handle(self):
        q = self.rfile.readline().strip().decode()
        self.server.queries.append(q)
        self.wfile.write(self.server.answers.get(q, "No match for this query\n").encode())


@pytest.fixture
def registry(settings):
    """Local RDAP + WHOIS servers. .com/.net have RDAP; .uk only WHOIS."""
    import json

    settings.private_target_allowlist = ["127.0.0.1/32"]
    exp = (datetime.now(UTC) + timedelta(days=200)).strftime("%Y-%m-%dT%H:%M:%SZ")
    wserver = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Whois)
    wserver.daemon_threads = True
    wserver.queries = []
    uk_exp = (TODAY + timedelta(days=90)).strftime("%d-%b-%Y")
    wserver.answers = {
        "uk": "domain:       UK\nwhois:        127.0.0.1\n",
        "example.co.uk": f"    Domain name:\n        example.co.uk\n    Registrar:\n        Nominet\n"
                         f"    Relevant dates:\n        Registered on: 26-Aug-1996\n        Expiry date:  {uk_exp}\n",
        "net": "whois: 127.0.0.1\n",
        "noexpiry.net": "Domain Name: NOEXPIRY.NET\nRegistrar: WhoisCo\n",
    }
    threading.Thread(target=wserver.serve_forever, daemon=True).start()
    with Site() as rdap:
        rdap.routes["/dns.json"] = {"type": "application/json", "body": json.dumps(
            {"version": "1.0", "services": [[["com", "net"], [f"{rdap.base}/rdap/"]]]})}
        rdap.routes["/rdap/domain/example.com"] = {"type": "application/rdap+json", "body": json.dumps(rdap_doc("example.com", exp))}
        rdap.routes["/rdap/domain/noexpiry.net"] = {"type": "application/rdap+json", "body": json.dumps(rdap_doc("noexpiry.net"))}
        settings.rdap_bootstrap_url = f"{rdap.base}/dns.json"
        settings.whois_server_iana = "127.0.0.1"
        settings.whois_port = wserver.server_address[1]
        yield rdap, wserver
    wserver.shutdown()
    wserver.server_close()


@pytest.fixture
def site_factory(make_client, make_website):
    def _f(url="https://www.example.com/", client=None, **kw):
        return make_website(client or make_client(), "Shop", url, notification_recipients=["ops@example.com"], **kw)
    return _f


# ---- lookups --------------------------------------------------------------------------------

@pytest.mark.parametrize("host, expected", [
    ("www.example.com", "example.com"), ("shop.example.co.uk", "example.co.uk"), ("example.com.au", "example.com.au"),
    ("a.b.github.io", "github.io"), ("127.0.0.1", None), ("xn--bcher-kva.de", "xn--bcher-kva.de"),
])
def test_registrable_domain(host, expected):
    assert lookup.registrable_domain(host) == expected


def test_parse_rdap_document():
    info = lookup.parse_rdap("example.com", rdap_doc("example.com", "2027-08-13T04:00:00Z"), "u")
    assert info.expires_at == datetime(2027, 8, 13, 4, tzinfo=UTC)
    assert info.registered_at.year == 1995 and info.registrar == "Example Registrar, Inc."
    assert info.nameservers == ["a.iana-servers.net", "b.iana-servers.net"]
    assert info.registrar_url == "https://registrar.example/"


def test_rdap_refresh_records_verified_data(db, registry, site_factory):
    rdap, _ = registry
    site = site_factory()
    rec = svc.refresh_domain(db, site)
    db.commit()
    assert (rec.domain, rec.source, rec.status) == ("example.com", "rdap", "ok")
    assert rec.registrar == "Example Registrar, Inc." and rec.verified_at is not None
    eff = svc.domain_effective(rec)
    assert eff.source == "rdap" and 199 <= eff.days_left() <= 200 and eff.note is None
    # bootstrap is cached: a second lookup does not fetch dns.json again
    svc.refresh_domain(db, site)
    assert [p for _, p in rdap.hits].count("/dns.json") == 1


def test_unregistered_domain(db, registry, site_factory):
    rec = svc.refresh_domain(db, site_factory("https://unregistered.com/"))
    assert rec.status == "error" and "not registered" in rec.last_error
    assert svc.domain_effective(rec).value is None


def test_whois_fallback_for_tld_without_rdap(db, registry, site_factory):
    _, whois = registry
    rec = svc.refresh_domain(db, site_factory("https://shop.example.co.uk/"))
    assert rec.source == "whois" and rec.status == "ok" and rec.registrar == "Nominet"
    assert rec.expires_at.date() == TODAY + timedelta(days=90)
    assert whois.queries == ["uk", "example.co.uk"]


def test_rdap_without_expiry_keeps_rdap_facts_when_whois_has_none(db, registry, site_factory):
    rec = svc.refresh_domain(db, site_factory("https://noexpiry.net/"))
    assert rec.source == "rdap" and rec.status == "unknown" and rec.registrar == "Example Registrar, Inc."
    eff = svc.domain_effective(rec)
    assert eff.value is None and "did not publish an expiry date" in eff.note


def test_whois_disabled(db, registry, site_factory, settings):
    settings.whois_enabled = False
    rec = svc.refresh_domain(db, site_factory("https://example.co.uk/"))
    assert rec.status == "error" and "WHOIS lookups are disabled" in rec.last_error


def test_failed_refresh_keeps_last_verified_value(db, registry, site_factory):
    rdap, _ = registry
    site = site_factory()
    svc.refresh_domain(db, site)
    del rdap.routes["/rdap/domain/example.com"]
    rdap.routes["/rdap/domain/example.com"] = {"status": 503, "body": "busy"}
    rec = svc.refresh_domain(db, site)
    eff = svc.domain_effective(rec)
    assert rec.status == "error" and eff.source == "rdap" and eff.value is not None
    assert eff.note.startswith("Latest lookup failed")


def test_manual_entry_is_separate_and_labelled(db, registry, site_factory):
    site = site_factory("https://unregistered.com/")
    svc.set_domain_manual(db, site, None, domain=None, expires_at=TODAY + timedelta(days=40), registrar="Manual Reg")
    rec = svc.refresh_domain(db, site)  # lookup fails -> manual stays the effective value
    eff = svc.domain_effective(rec)
    assert eff.source == "manual" and eff.days_left() == 40 and "not verified" in eff.note
    assert rec.manual_expires_at is not None  # never overwritten by lookups


def test_domain_override_resets_automated_data(db, registry, site_factory):
    site = site_factory("https://www.example.com/")
    svc.refresh_domain(db, site)
    rec = svc.set_domain_manual(db, site, None, domain="example.co.uk", expires_at=None, registrar=None)
    assert rec.domain_overridden and rec.verified_at is None and rec.expires_at is None
    rec = svc.refresh_domain(db, site)
    assert rec.domain == "example.co.uk" and rec.source == "whois"


def test_ip_address_site_has_no_domain(db, site_factory, settings):
    settings.private_target_allowlist = ["127.0.0.1/32"]
    rec = svc.refresh_domain(db, site_factory("https://127.0.0.1:8443/"))
    assert rec.status == "unknown" and "IP address" in rec.last_error


# ---- alerts -----------------------------------------------------------------------------------

def _set_domain(db, site, days):
    rec = svc.get_domain_record(db, site)
    rec.expires_at = datetime.combine(TODAY + timedelta(days=days), datetime.min.time(), UTC)
    rec.verified_at, rec.source, rec.status = datetime.now(UTC), "rdap", "ok"
    db.commit()
    return rec


def subjects():
    return [m["subject"] for m in MemoryNotifier.outbox]


def run_alerts(db, today=None):
    from app.worker.tasks import send_notification

    for i in svc.send_expiry_alerts(db, today=today):
        send_notification.delay(i)


def test_domain_alert_thresholds_once_each(db, site_factory):
    site = site_factory("http://www.example.com/")  # http: no SSL noise
    _set_domain(db, site, 25)
    run_alerts(db)
    run_alerts(db)
    assert subjects() == ["[SiteWatch] Domain registration expires in 25 days: Shop"]
    log = db.scalar(select(NotificationLog).where(NotificationLog.event_type == "domain_expiry"))
    assert log.dedup_key.endswith(":30")  # only the tightest threshold crossed (30), not 60 as well
    run_alerts(db, today=TODAY + timedelta(days=12))  # 13 days left -> crosses 14
    assert subjects()[-1] == "[SiteWatch] Domain registration expires in 13 days: Shop"
    run_alerts(db, today=TODAY + timedelta(days=26))  # expired
    assert subjects()[-1] == "[SiteWatch] Domain registration EXPIRED: Shop"
    assert len(subjects()) == 3
    _set_domain(db, site, 20)  # renewed to a new date -> new cycle
    run_alerts(db)
    assert len(subjects()) == 4 and "verified via RDAP" in MemoryNotifier.outbox[-1]["body"]


def test_no_alert_when_unknown_or_far(db, site_factory):
    site = site_factory("http://www.example.com/")
    run_alerts(db)
    _set_domain(db, site, 300)
    run_alerts(db)
    assert subjects() == []


def test_threshold_rules_override_defaults(db, site_factory):
    site = site_factory("http://www.example.com/")
    db.add(NotificationRule(event_type="domain_expiry", website_id=site.id, thresholds={"days_before": [90]}))
    db.commit()
    _set_domain(db, site, 80)
    run_alerts(db)
    assert subjects() == ["[SiteWatch] Domain registration expires in 80 days: Shop"]


def test_manual_hosting_alert_says_unverified(db, site_factory):
    site = site_factory("http://www.example.com/")
    rec = svc.get_hosting_record(db, site, create=True)
    rec.renewal_date, rec.source, rec.provider = TODAY + timedelta(days=6), "manual", "Acme Hosting"
    db.commit()
    run_alerts(db)
    assert subjects() == ["[SiteWatch] Hosting expires in 6 days: Shop"]
    assert "entered manually (not verified)" in MemoryNotifier.outbox[0]["body"]


# ---- SSL ----------------------------------------------------------------------------------

def test_ssl_refresh_invalid_certificate_and_alert(db, site_factory, settings):
    from tests.httpfixtures import LocalSite

    settings.private_target_allowlist = ["127.0.0.1/32"]
    with LocalSite(tls=True) as s:
        site = site_factory(s.base + "/")
        row = svc.refresh_ssl(db, site)
    db.commit()
    assert row.valid is False and row.not_after is not None and row.check_error is None
    run_alerts(db)
    run_alerts(db)
    assert subjects() == ["[SiteWatch] SSL certificate is not valid: Shop"]
    # a later check that cannot connect does not erase the last real observation
    settings.private_target_allowlist = []
    failed = svc.refresh_ssl(db, site)
    db.commit()
    assert failed.check_error and svc.latest_ssl(db, site.id, completed_only=True).id == row.id


def test_refresh_due_is_rate_limited(db, registry, site_factory, settings):
    from tests.httpfixtures import LocalSite

    with LocalSite(tls=True) as s:
        site_factory(s.base + "/")
        site_factory("https://www.example.com/")
        first = svc.refresh_due(db)
        second = svc.refresh_due(db)
    assert first["domains"] == 2 and first["ssl"] == 2
    assert second == {"domains": 0, "ssl": 0, "hosting": 0}


# ---- hosting provider adapters -----------------------------------------------------------------

class FakeProvider:
    key, display_name, credential_fields = "fakehost", "Fake Host", ["api_token"]
    seen: list = []

    def fetch(self, credentials, account_reference):
        FakeProvider.seen.append((credentials, account_reference))
        return hosting.HostingLookup(renewal_date=TODAY + timedelta(days=100), plan="Pro")


@pytest.fixture
def fake_provider(settings):
    settings.encryption_key = Fernet.generate_key().decode()
    hosting.register(FakeProvider())
    FakeProvider.seen = []
    yield
    hosting.REGISTRY.pop("fakehost", None)


def test_hosting_api_integration_flow(db, site_factory, as_user, fake_provider):
    admin = as_user("super_admin")
    site = site_factory()
    assert admin.get("/api/v1/integrations/hosting/providers").json()[0]["key"] == "fakehost"
    assert admin.post("/api/v1/integrations/hosting", json={"provider_key": "fakehost", "display_name": "x",
                                                            "credentials": {}}).status_code == 422
    r = admin.post("/api/v1/integrations/hosting", json={"provider_key": "fakehost", "display_name": "Main account",
                                                         "credentials": {"api_token": "s3cret-token"}})
    assert r.status_code == 201
    integ = db.get(HostingIntegration, __import__("uuid").UUID(r.json()["id"]))
    assert b"s3cret-token" not in integ.encrypted_credentials
    assert "s3cret" not in admin.get("/api/v1/integrations/hosting").text
    admin.put(f"/api/v1/websites/{site.id}/hosting", json={"provider": "Fake Host", "account_reference": "acct-9",
                                                           "integration_id": r.json()["id"]})
    h = admin.post(f"/api/v1/websites/{site.id}/hosting/refresh").json()
    assert h["source"] == "api" and h["effective"]["source"] == "api" and h["plan"] == "Pro"
    assert FakeProvider.seen == [({"api_token": "s3cret-token"}, "acct-9")]
    # a person typing a date makes it manual again
    h = admin.put(f"/api/v1/websites/{site.id}/hosting", json={"renewal_date": str(TODAY + timedelta(days=5))}).json()
    assert h["source"] == "manual" and h["verified_at"] is None and h["entered_by"] == admin.user.email


def test_integration_requires_encryption_key(as_user, fake_provider, settings):
    settings.encryption_key = ""
    r = as_user("super_admin").post("/api/v1/integrations/hosting", json={
        "provider_key": "fakehost", "display_name": "x", "credentials": {"api_token": "t"}})
    assert r.status_code == 503 and "ENCRYPTION_KEY" in r.json()["detail"]


def test_no_providers_installed_by_default(as_user):
    admin = as_user("super_admin")
    assert admin.get("/api/v1/integrations/hosting/providers").json() == []
    r = admin.post("/api/v1/integrations/hosting", json={"provider_key": "cpanel", "display_name": "x", "credentials": {}})
    assert r.status_code == 422 and "manual renewal dates" in r.json()["detail"]


def test_refresh_without_integration_is_conflict(as_user, site_factory):
    dev = as_user("developer")
    site = site_factory()
    site.assigned_users = [dev.user]
    from sqlalchemy.orm import object_session
    object_session(site).commit()
    r = dev.post(f"/api/v1/websites/{site.id}/hosting/refresh")
    assert r.status_code == 409 and "No provider integration" in r.json()["detail"]


# ---- API permissions & client views -----------------------------------------------------------

@pytest.fixture
def world(db, make_client, make_website, as_user):
    shown, hidden = make_client("Shown", show_expiry=True), make_client("Hidden", show_expiry=False)
    dev, admin = as_user("developer"), as_user("super_admin")
    cu_shown, cu_hidden = as_user("client", shown), as_user("client", hidden)
    a = make_website(shown, "A", "http://a.example.com/", assign=[dev.user, cu_shown.user])
    b = make_website(hidden, "B", "http://b.example.com/", assign=[cu_hidden.user])
    for site in (a, b):
        rec = svc.get_domain_record(db, site)
        rec.manual_expires_at = datetime.combine(TODAY + timedelta(days=45), datetime.min.time(), UTC)
        rec.manual_entered_at = datetime.now(UTC)
        h = svc.get_hosting_record(db, site, create=True)
        h.provider, h.account_reference, h.internal_notes = "Acme Hosting", "ACCT-SECRET-1", "root pw in vault #42"
        h.renewal_date, h.source = TODAY + timedelta(days=10), "manual"
    db.commit()
    return dict(dev=dev, admin=admin, cu_shown=cu_shown, cu_hidden=cu_hidden, a=a, b=b)


def test_client_expiry_visibility_follows_client_setting(world):
    w = world
    r = w["cu_shown"].get(f"/api/v1/websites/{w['a'].id}/expiry")
    assert r.status_code == 200
    body = r.json()
    assert body["domain"]["effective"]["source"] == "manual" and body["domain"]["effective"]["days_left"] == 45
    assert body["hosting"]["effective"]["level"] == "critical" and body["hosting"]["provider"] == "Acme Hosting"
    for secret in ("ACCT-SECRET-1", "root pw", "lookup", "manual\":", "internal_notes", "account_reference"):
        assert secret not in r.text
    assert w["cu_hidden"].get(f"/api/v1/websites/{w['b'].id}/expiry").status_code == 403
    assert w["cu_hidden"].get("/api/v1/expiry/overview").json()["websites"] == []
    assert [x["website_name"] for x in w["cu_shown"].get("/api/v1/expiry/overview").json()["websites"]] == ["A"]
    assert w["cu_shown"].get(f"/api/v1/websites/{w['b'].id}/expiry").status_code == 404


def test_staff_view_and_write_permissions(world):
    w = world
    staff = w["dev"].get(f"/api/v1/websites/{w['a'].id}/expiry").json()
    assert staff["hosting"]["account_reference"] == "ACCT-SECRET-1" and staff["domain"]["manual"]["expires_at"]
    assert w["dev"].get(f"/api/v1/websites/{w['b'].id}/expiry").status_code == 404
    assert w["cu_shown"].put(f"/api/v1/websites/{w['a'].id}/hosting", json={"provider": "x"}).status_code == 403
    assert w["cu_shown"].put(f"/api/v1/websites/{w['a'].id}/domain", json={"domain": "x.com"}).status_code == 403
    assert w["dev"].post("/api/v1/integrations/hosting", json={}).status_code in (403, 422)
    assert w["dev"].get("/api/v1/integrations/hosting").status_code == 403
    bad = w["dev"].put(f"/api/v1/websites/{w['a'].id}/domain", json={"domain": "not a domain"})
    assert bad.status_code == 422
    sub = w["dev"].put(f"/api/v1/websites/{w['a'].id}/domain", json={"domain": "shop.example.com"})
    assert sub.status_code == 422  # must be the registrable domain
    ok = w["dev"].put(f"/api/v1/websites/{w['a'].id}/domain",
                      json={"manual_expires_at": str(TODAY + timedelta(days=99)), "manual_registrar": "GoDaddy"})
    assert ok.json()["effective"]["days_left"] == 99 and ok.json()["manual"]["entered_by"] == w["dev"].user.email


def test_ssl_refresh_endpoint_rules(world):
    w = world
    r = w["dev"].post(f"/api/v1/websites/{w['a'].id}/ssl/refresh")
    assert r.status_code == 409  # http site
    staff = w["dev"].get(f"/api/v1/websites/{w['a'].id}/expiry").json()
    assert staff["ssl"]["applicable"] is False


def test_refresh_endpoints_rate_limited(world, registry):
    w = world
    assert w["dev"].post(f"/api/v1/websites/{w['a'].id}/domain/refresh").status_code == 200
    assert w["dev"].post(f"/api/v1/websites/{w['a'].id}/domain/refresh").status_code == 429


def test_ssl_rows_are_history(db, world):
    rows = db.scalars(select(SslCertificate)).all()
    assert rows == []  # nothing is invented before a check runs
    assert db.scalar(select(DomainRecord).where(DomainRecord.website_id == world["a"].id)).verified_at is None
