"""Offline self-check for the reconcile logic and label parsing.

Run from tunnel-manager/:  python3 test_reconcile.py

Uses fake API clients and fake containers: no network calls, no Cloudflare
state is touched, and no Docker daemon is needed.
"""
import logging
from contextlib import contextmanager
from types import SimpleNamespace

from cloudflare.types.zero_trust.tunnels.configuration_get_response import (
    ConfigIngress,
    ConfigIngressOriginRequest,
)
from cloudflare_manager import CloudflareManager, MANAGED_COMMENTS
from docker_manager import DockerManager

DOMAIN = 'example.com'
HOST_IP = '10.0.0.5'
CATCH_ALL = dict(hostname='', service='http_status:404')
MANAGED = MANAGED_COMMENTS[0]


def record(name, comment=MANAGED, content='tunnel.cfargotunnel.com', proxied=True, ttl=1):
    return SimpleNamespace(
        id=name, name=name, comment=comment,
        content=content, proxied=proxied, ttl=ttl, modified_on='now',
    )


class FakeRecords:
    def __init__(self):
        self.records = []
        self.created = []
        self.edited = []
        self.deleted = []

    def list(self, **kwargs):
        if 'name' in kwargs:
            return [r for r in self.records if r.name == kwargs['name']['exact']]
        return list(self.records)

    def create(self, **kwargs):
        rec = record(kwargs['name'], kwargs['comment'], kwargs['content'],
                     kwargs['proxied'], kwargs['ttl'])
        self.records.append(rec)
        self.created.append(rec)
        return rec

    def edit(self, dns_record_id, **kwargs):
        rec = next(r for r in self.records if r.id == dns_record_id)
        for key in ('name', 'comment', 'content', 'proxied', 'ttl'):
            setattr(rec, key, kwargs[key])
        self.edited.append(rec)
        return rec

    def delete(self, dns_record_id, **kwargs):
        rec = next(r for r in self.records if r.id == dns_record_id)
        self.records.remove(rec)
        self.deleted.append(rec)
        return SimpleNamespace(id=dns_record_id)


class FakeConfigurations:
    def __init__(self, ingress, source='cloudflare'):
        self.ingress = ingress
        self.source = source
        self.pushes = 0

    def get(self, **kwargs):
        return SimpleNamespace(
            source=self.source,
            config=SimpleNamespace(
                ingress=list(self.ingress), origin_request=None, warp_routing=None
            )
        )

    def update(self, account_id, tunnel_id, config):
        self.pushes += 1
        self.ingress = config.ingress
        return SimpleNamespace(config=config)


def build(ingress=None, records=None, source='cloudflare'):
    """Construct a manager without __init__ so no real client is created."""
    manager = CloudflareManager.__new__(CloudflareManager)
    manager.domain = DOMAIN
    manager.host_ip = HOST_IP
    manager.zone_id = 'zone'
    manager.account_id = 'account'
    manager.tunnel_id = 'tunnel'
    manager.dns_record_cache = {}
    manager.tunnel_config_cache = None
    # Tests exercise steady-state behaviour; the first-pass prune guard has its
    # own test.
    manager._has_reconciled = True
    manager._warned_local_source = False
    manager.cf = SimpleNamespace(
        dns=SimpleNamespace(records=FakeRecords()),
        zero_trust=SimpleNamespace(
            tunnels=SimpleNamespace(
                configurations=FakeConfigurations(ingress or [], source=source)
            )
        ),
    )
    manager.cf.dns.records.records.extend(records or [])
    return manager


def hosts(manager):
    return [rule.hostname for rule in manager.tunnel_config_cache.config.ingress]


def enabled(subdomain, port='80'):
    return {'enabled': True, 'subdomain': subdomain, 'port': port}


def test_adds_dns_and_ingress_before_catch_all():
    m = build(ingress=[ConfigIngress(**CATCH_ALL)])
    assert m.reconcile({'app': enabled('app', '8080')}) is True
    assert hosts(m) == ['app.example.com', ''], hosts(m)
    assert [r.name for r in m.cf.dns.records.created] == ['app.example.com']
    assert m.cf.zero_trust.tunnels.configurations.pushes == 1


def test_update_preserves_origin_request():
    origin = ConfigIngressOriginRequest(keepAliveTimeout=30)
    m = build(
        ingress=[
            ConfigIngress(hostname='app.example.com',
                          service=f'http://{HOST_IP}:80', originRequest=origin),
            ConfigIngress(**CATCH_ALL),
        ],
        records=[record('app.example.com')],
    )
    assert m.reconcile({'app': enabled('app', '9090')}) is True
    rule = next(r for r in m.tunnel_config_cache.config.ingress
                if r.hostname == 'app.example.com')
    assert rule.service == f'http://{HOST_IP}:9090'
    assert rule.origin_request.keep_alive_timeout == 30
    assert not m.cf.dns.records.edited


def test_unchanged_state_is_a_noop():
    m = build(
        ingress=[
            ConfigIngress(hostname='app.example.com', service=f'http://{HOST_IP}:80'),
            ConfigIngress(**CATCH_ALL),
        ],
        records=[record('app.example.com')],
    )
    assert m.reconcile({'app': enabled('app')}) is False
    assert m.cf.zero_trust.tunnels.configurations.pushes == 0
    assert not m.cf.dns.records.created
    assert not m.cf.dns.records.edited


def test_prunes_stale_managed_records_only():
    m = build(
        ingress=[
            ConfigIngress(hostname='old.example.com', service=f'http://{HOST_IP}:80'),
            ConfigIngress(**CATCH_ALL),
        ],
        records=[
            record('old.example.com'),
            record('manual.example.com', comment='hand made'),
        ],
    )
    assert m.reconcile({}) is True
    assert [r.name for r in m.cf.dns.records.deleted] == ['old.example.com']
    assert hosts(m) == [''], hosts(m)


def test_legacy_comment_is_pruned():
    m = build(records=[record('old.example.com', comment=MANAGED_COMMENTS[1])])
    assert m.reconcile({}) is True
    assert [r.name for r in m.cf.dns.records.deleted] == ['old.example.com']


def test_first_pass_does_not_prune():
    m = build(records=[record('old.example.com')])
    m._has_reconciled = False
    assert m.reconcile({}) is False
    assert m.cf.dns.records.deleted == []
    assert m.reconcile({}) is True
    assert [r.name for r in m.cf.dns.records.deleted] == ['old.example.com']


def test_keeps_unmanaged_ingress_rule():
    m = build(
        ingress=[
            ConfigIngress(hostname='other.example.com', service='http://192.168.1.9:3000'),
            ConfigIngress(**CATCH_ALL),
        ]
    )
    assert m.reconcile({}) is False
    assert hosts(m) == ['other.example.com', '']


def test_adopts_manual_record_for_labelled_container():
    m = build(records=[record('app.example.com', comment='hand made')])
    assert m.reconcile({'app': enabled('app')}) is True
    assert [r.comment for r in m.cf.dns.records.edited] == [MANAGED]


def test_die_removes_dns_and_ingress():
    m = build(
        ingress=[
            ConfigIngress(hostname='app.example.com', service=f'http://{HOST_IP}:80'),
            ConfigIngress(**CATCH_ALL),
        ],
        records=[record('app.example.com')],
    )
    # Reconcile first, as the loop does, so the cache is warm.
    m.reconcile({'app': enabled('app')})
    assert m.handle_container_update(enabled('app'), 'die') is True
    assert hosts(m) == [''], hosts(m)
    assert [r.name for r in m.cf.dns.records.deleted] == ['app.example.com']
    assert m.cf.zero_trust.tunnels.configurations.pushes == 1


class FakeContainer:
    def __init__(self, name, labels, ports=None):
        self.name = name
        self.labels = labels
        self.ports = ports or {}


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())


@contextmanager
def capture_warnings(logger_name):
    handler = _Capture()
    target = logging.getLogger(logger_name)
    target.addHandler(handler)
    try:
        yield handler.messages
    finally:
        target.removeHandler(handler)


def labels_for(container_or_event):
    return DockerManager.__new__(DockerManager).get_container_labels(container_or_event)


def test_labels_use_explicit_port():
    labels = labels_for(FakeContainer('web', {
        'cloudflare.enabled': 'true',
        'cloudflare.subdomain': 'app',
        'cloudflare.port': '8080',
    }))
    assert labels == {'enabled': True, 'subdomain': 'app', 'port': '8080'}


def test_labels_default_subdomain_and_single_port():
    labels = labels_for(FakeContainer(
        'web', {'cloudflare.enabled': 'true'},
        {'80/tcp': [{'HostPort': '8080'}]},
    ))
    assert labels == {'enabled': True, 'subdomain': 'web', 'port': '8080'}


def test_labels_warn_on_multiple_published_ports():
    container = FakeContainer(
        'web', {'cloudflare.enabled': 'true'},
        {'80/tcp': [{'HostPort': '8080'}], '443/tcp': [{'HostPort': '8443'}]},
    )
    with capture_warnings('docker_manager') as messages:
        labels = labels_for(container)
    assert labels['port'] == '8443'
    assert len(messages) == 1 and 'cloudflare.port' in messages[0], messages


def test_labels_disabled_container():
    assert labels_for(FakeContainer('db', {'cloudflare.enabled': 'false'})) == {
        'enabled': False
    }


def test_labels_from_die_event():
    labels = labels_for({'Actor': {'Attributes': {
        'name': 'web',
        'cloudflare.enabled': 'true',
        'cloudflare.subdomain': 'app',
    }}})
    assert labels == {'enabled': True, 'subdomain': 'app', 'port': '80'}


def test_labels_invalid_event_returns_empty_dict():
    with capture_warnings('docker_manager'):
        assert labels_for({}) == {}


def test_warns_once_for_local_tunnel_config():
    m = build(source='local')
    with capture_warnings('cloudflare_manager') as messages:
        m.get_tunnel_config()
        m.get_tunnel_config()
    assert len(messages) == 1, messages
    assert 'local' in messages[0]


if __name__ == '__main__':
    tests = [value for name, value in sorted(globals().items())
             if name.startswith('test_')]
    for test in tests:
        test()
        print(f'ok   {test.__name__}')
    print(f'\n{len(tests)} passed')
