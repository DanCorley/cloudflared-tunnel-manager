import json
import base64
import logging

from cloudflare import Cloudflare
from cloudflare.types.zero_trust.tunnels.configuration_get_response import (
    ConfigurationGetResponse,
    ConfigIngress,
)

logger = logging.getLogger(__name__)

# Comments stamped on records this tool created. Used to tell our records apart
# from hand-managed ones so stale ones can be pruned safely.
MANAGED_COMMENTS = (
    'managed via cloudflared-tunnel-manager',
    'updated via dns-manager',  # written by an earlier scratch script
)


class CloudflareManager:
    def __init__(self, api_token, account_id, tunnel_token, zone_id, domain, host_ip='localhost'):
        self.api_token = api_token
        self.account_id = account_id
        self.tunnel_token = tunnel_token
        self.zone_id = zone_id
        self.domain = domain
        self.dns_record_cache = {}
        self.tunnel_config_cache = None
        self.host_ip = host_ip
        self._has_reconciled = False

        # Extract tunnel ID and initialize client
        self.tunnel_id = self._get_tunnel_id_from_token()
        self.cf = Cloudflare(api_token=api_token)
        logger.info("Successfully initialized Cloudflare client")

        # Caches are filled on the first reconcile, not here, so startup only reads Cloudflare state once.

    def _get_tunnel_id_from_token(self):
        """Extract tunnel ID from Cloudflare tunnel token."""
        try:
            tunnel_id = json.loads(
                base64.b64decode(
                    self.tunnel_token + '=' * (
                    -len(self.tunnel_token) % 4
                    )
                )
            ).get('t')

            if not tunnel_id:
                raise ValueError("No tunnel ID found in token")

            logger.info(f"Successfully extracted tunnel ID from token")
            return tunnel_id

        except Exception as e:
            logger.error(f"Failed to extract tunnel ID from token: {str(e)}")
            raise

    def get_dns_records(self):
        """Refresh and return the cache of CNAME records for the domain."""
        try:
            records = self.cf.dns.records.list(
                zone_id=self.zone_id,
                type='CNAME'
            )

            self.dns_record_cache = {
                record.name.replace(f'.{self.domain}', ''): record
                for record in records
                if record.name.endswith(self.domain)
            }
            logger.debug(f"Cached {len(self.dns_record_cache)} DNS records")
            return self.dns_record_cache

        except Exception as e:
            logger.error(f"Error fetching DNS records: {str(e)}")
            raise

    def get_tunnel_config(self) -> ConfigurationGetResponse:
        """Get current tunnel configuration and cache it."""
        try:
            config = self.cf.zero_trust.tunnels.configurations.get(
                account_id=self.account_id,
                tunnel_id=self.tunnel_id
            )
            self.tunnel_config_cache = config
            logger.debug("Successfully cached tunnel configuration")
            return config
        except Exception as e:
            logger.error(f"Error getting tunnel configuration: {str(e)}")
            raise

    def update_tunnel_config(self, labels: dict, action: str = 'start') -> bool:
        """Apply a container's labels to the cached ingress rules.

        Returns True when the cache changed.
        """
        if not labels.get('enabled'):
            logger.debug("Cannot update tunnel config: not enabled in labels")
            return False

        if not self.tunnel_config_cache:
            self.get_tunnel_config()

        subdomain = labels.get('subdomain')
        hostname = f"{subdomain}.{self.domain}"
        config = self.tunnel_config_cache

        existing_rule = next(
            (rule for rule in config.config.ingress
             if rule.hostname == hostname),
            None
        )

        if action == 'die':
            if not existing_rule:
                return False
            config.config.ingress.remove(existing_rule)
            logger.info(f"Removed ingress rule for {hostname}")
            return True

        service = f"http://{self.host_ip}:{labels.get('port', '80')}"

        if existing_rule:
            # Mutate in place so hand-configured originRequest/path survive.
            if existing_rule.service == service:
                return False
            existing_rule.service = service
            logger.info(f"Updated ingress rule for {hostname}")
            return True

        # Add before the catch-all rule, which must stay last.
        config.config.ingress.insert(
            -1,
            ConfigIngress(hostname=hostname, service=service)
        )
        logger.info(f"Added new ingress rule for {hostname}")
        return True

    def push_tunnel_config(self) -> None:
        """Push the cached tunnel configuration to Cloudflare."""
        try:
            if not self.tunnel_config_cache:
                logger.warning("No tunnel configuration cache to push")
                return

            logger.info("Pushing tunnel configuration to Cloudflare")
            self.cf.zero_trust.tunnels.configurations.update(
                account_id=self.account_id,
                tunnel_id=self.tunnel_id,
                config=self.tunnel_config_cache.config
            )
            logger.info("Successfully pushed tunnel configuration")

        except Exception as e:
            logger.error(f"Error pushing tunnel configuration: {str(e)}")
            raise

    def update_dns_record(self, labels: dict, action: str = 'start') -> bool:
        """Apply a container's labels to its DNS record.

        Returns True when a record was created, edited or deleted.
        """
        if not labels.get('enabled'):
            logger.debug("Cannot update DNS record: not enabled in labels")
            return False

        subdomain = labels.get('subdomain')
        hostname = f"{subdomain}.{self.domain}"

        current_record = self.dns_record_cache.get(subdomain)

        if action == 'die':
            if current_record is None:
                return False
            logger.info(f"Deleting DNS record for {hostname}")
            self.cf.dns.records.delete(
                zone_id=self.zone_id,
                dns_record_id=current_record.id
            )
            del self.dns_record_cache[subdomain]
            return True

        record_data = {
            'comment': MANAGED_COMMENTS[0],
            'content': f'{self.tunnel_id}.cfargotunnel.com',
            'name': hostname,
            'proxied': labels.get('proxied', 'true').lower() == 'true',
            'ttl': int(labels.get('ttl', 1)),
            'type': 'CNAME'
        }

        if current_record is None:
            logger.info(f"Creating new DNS record for {hostname}")
            self.dns_record_cache[subdomain] = self.cf.dns.records.create(
                zone_id=self.zone_id,
                **record_data
            )
            return True

        # Compare the fields we own instead of timestamps: no extra API call,
        # and it still corrects out-of-band edits to content/proxied/ttl.
        # Comment is included so a labelled container adopts a hand-made record
        # and it becomes prunable later.
        if (current_record.content != record_data['content']
                or current_record.proxied != record_data['proxied']
                or current_record.ttl != record_data['ttl']
                or current_record.comment != record_data['comment']):
            logger.info(f"Updating DNS record for {hostname}")
            self.dns_record_cache[subdomain] = self.cf.dns.records.edit(
                zone_id=self.zone_id,
                dns_record_id=current_record.id,
                **record_data
            )
            return True

        return False

    def handle_container_update(self, labels: dict, action: str = 'start') -> bool:
        """Handle both DNS and tunnel configuration updates for a container."""
        try:
            if not labels.get('enabled', False):
                logger.debug("Skipping update for disabled container")
                return False

            dns_changed = self.update_dns_record(labels, action)
            tunnel_changed = self.update_tunnel_config(labels, action)
            if tunnel_changed:
                self.push_tunnel_config()

            return dns_changed or tunnel_changed

        except Exception as e:
            logger.error(f"Error handling container update for container '{labels.get('subdomain')}': {str(e)}")
            raise

    def _is_managed_rule(self, rule: ConfigIngress) -> bool:
        # shortcut: ingress rules carry no owner marker, so ownership is inferred
        # from hostname and service shape. Upgrade if Cloudflare adds a marker.
        return (
            bool(rule.hostname)
            and rule.hostname.endswith(f'.{self.domain}')
            and rule.service.startswith(f'http://{self.host_ip}:')
        )

    def _reconcile_dns(self, desired: dict, allow_prune: bool) -> bool:
        changed = False
        for labels in desired.values():
            if self.update_dns_record(labels):
                changed = True

        if not allow_prune:
            return changed

        for subdomain, record in list(self.dns_record_cache.items()):
            if subdomain in desired:
                continue
            if record.comment not in MANAGED_COMMENTS:
                continue
            logger.info(f"Removing stale DNS record for {record.name}")
            self.cf.dns.records.delete(
                zone_id=self.zone_id,
                dns_record_id=record.id
            )
            del self.dns_record_cache[subdomain]
            changed = True

        return changed

    def _reconcile_tunnel(self, desired: dict, allow_prune: bool) -> bool:
        changed = False
        for labels in desired.values():
            if self.update_tunnel_config(labels):
                changed = True

        if not allow_prune:
            return changed

        desired_hostnames = {f"{subdomain}.{self.domain}" for subdomain in desired}
        for rule in list(self.tunnel_config_cache.config.ingress):
            if rule.hostname in desired_hostnames:
                continue
            if not self._is_managed_rule(rule):
                continue
            logger.info(f"Removing stale ingress rule for {rule.hostname}")
            self.tunnel_config_cache.config.ingress.remove(rule)
            changed = True

        return changed

    def reconcile(self, desired: dict) -> bool:
        """Converge Cloudflare to `desired` (subdomain -> labels).

        Refreshes state first so out-of-band changes are corrected, then applies
        the smallest set of writes. Returns True when anything changed.
        """
        self.get_dns_records()
        self.get_tunnel_config()

        # Prune only from the second pass. On a host reboot the sibling
        # containers may not have started yet, and pruning then would delete
        # every managed record.
        allow_prune = self._has_reconciled
        self._has_reconciled = True

        logger.debug(f"Reconciling {len(desired)} desired hostnames")
        dns_changed = self._reconcile_dns(desired, allow_prune)
        tunnel_changed = self._reconcile_tunnel(desired, allow_prune)

        if tunnel_changed:
            self.push_tunnel_config()

        return dns_changed or tunnel_changed
