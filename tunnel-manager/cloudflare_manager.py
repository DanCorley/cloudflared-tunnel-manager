import os
import json
import base64
import logging
from cloudflare import Cloudflare
from cloudflare.types.zero_trust.tunnels.configuration_get_response import (
    ConfigurationGetResponse,
    Config,
    ConfigIngress
)

logger = logging.getLogger('dns-manager')

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
        
        # Extract tunnel ID and initialize client
        self.tunnel_id = self._get_tunnel_id_from_token()
        self.cf = Cloudflare(api_token=api_token)
        logger.info("Successfully initialized Cloudflare client")
        
        # Initialize caches
        self.get_dns_records()
        self.get_tunnel_config()

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

    def get_dns_records(self, search=None):
        """Get DNS records from Cloudflare.
        
        If search is provided, returns a single matching record.
        Otherwise, caches and returns all CNAME records for the domain.
        """
        try:
            records = self.cf.dns.records.list(
                zone_id=self.zone_id,
                type='CNAME',
                search=search
            )

            if search:
                # Return first matching record if searching
                matching = [r for r in records if r.name.endswith(self.domain)]
                return matching[0] if matching else None
            else:
                # Cache all domain records if not searching
                dns_records = [
                    record for record in records
                    if record.name.endswith(self.domain)
                ]
                
                # Update the cache
                self.dns_record_cache = {
                    record.name.replace(f'.{self.domain}', ''): record
                    for record in dns_records
                }
                logger.info(f"Cached {len(self.dns_record_cache)} DNS records")
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
            logger.info("Successfully cached tunnel configuration")
            return config
        except Exception as e:
            logger.error(f"Error getting tunnel configuration: {str(e)}")
            raise

    def update_tunnel_config(self, labels: dict) -> None:
        """Update tunnel configuration cache based on container labels."""
        try:
            if not self.tunnel_config_cache:
                self.get_tunnel_config()

            config = self.tunnel_config_cache
            subdomain = labels['subdomain']
            hostname = f"{subdomain}.{self.domain}"
            logger.info(f"Hostname for {subdomain}: {hostname}")
            
            # Find existing ingress rule for this hostname
            existing_rule = next(
                (rule for rule in config.config.ingress 
                 if rule.hostname == hostname),
                None
            )

            # If container is disabled, remove the ingress rule
            if labels.get('enabled', 'true').lower() != 'true':
                if existing_rule:
                    config.config.ingress.remove(existing_rule)
                    logger.info(f"Removed ingress rule for {hostname}")
            else:
                # Create or update ingress rule
                service = f"http://{self.host_ip}:{labels.get('port', '80')}"
                new_rule = ConfigIngress(
                    hostname=hostname,
                    service=service,
                    origin_request=None,
                    path=None
                )

                if existing_rule:
                    # Update existing rule
                    idx = config.config.ingress.index(existing_rule)
                    config.config.ingress[idx] = new_rule
                    logger.info(f"Updated ingress rule for {hostname}")
                else:
                    # Add new rule before catch-all rule (the last rule)
                    config.config.ingress.insert(-2, new_rule)
                    logger.info(f"Added new ingress rule for {hostname}")

            # Update local cache only
            self.tunnel_config_cache = config
            logger.debug(f"Updated tunnel configuration cache for {hostname}")

        except Exception as e:
            logger.error(f"Error updating tunnel configuration cache: {str(e)}")
            raise

    def push_tunnel_config(self) -> None:
        """Push the cached tunnel configuration to Cloudflare."""
        try:
            if not self.tunnel_config_cache:
                logger.warning("No tunnel configuration cache to push")
                return

            logger.info("Pushing tunnel configuration to Cloudflare")
            result = self.cf.zero_trust.tunnels.configurations.update(
                account_id=self.account_id,
                tunnel_id=self.tunnel_id,
                config=self.tunnel_config_cache.config
            )
            logger.info("Successfully pushed tunnel configuration")

        except Exception as e:
            logger.error(f"Error pushing tunnel configuration: {str(e)}")
            raise

    def update_dns_record(self, labels):
        """Update a single DNS record based on container labels."""
        try:
            subdomain = labels['subdomain']
            active = labels.get('enabled', 'true').lower() == 'true'

            # Prepare record data using labels
            record_data = {
                'comment': 'managed via cloudflared-tunnel-manager',
                'content': f'{self.tunnel_id}.cfargotunnel.com',
                'name': f"{subdomain}.{self.domain}",
                'proxied': labels.get('proxied', 'true').lower() == 'true',
                'ttl': int(labels.get('ttl', 1)),
                'type': 'CNAME'
            }

            # Get current state from Cloudflare
            current_record = self.get_dns_records(search=subdomain)
            in_cloudflare = current_record is not None
            in_cache = subdomain in self.dns_record_cache

            if not active:
                # Handle disabled state - delete if exists
                if in_cache or in_cloudflare:
                    logger.info(f"Deleting DNS record for {subdomain}.{self.domain}")
                    if in_cloudflare:
                        self.cf.dns.records.delete(
                            zone_id=self.zone_id,
                            dns_record_id=current_record.id
                        )
                    if in_cache:
                        del self.dns_record_cache[subdomain]
                return

            # Handle enabled state for DNS
            if in_cloudflare:
                if not in_cache:
                    # Record exists in Cloudflare but not in cache
                    logger.info(f"Adding existing DNS record for {subdomain}.{self.domain} to cache")
                    self.dns_record_cache[subdomain] = current_record
                
                # Check if update needed based on modified timestamp
                cached_record = self.dns_record_cache[subdomain]
                if current_record.modified_on != cached_record.modified_on:
                    logger.info(f"Updating DNS record for {subdomain}.{self.domain}")
                    updated = self.cf.dns.records.edit(
                        zone_id=self.zone_id,
                        dns_record_id=current_record.id,
                        **record_data
                    )
                    self.dns_record_cache[subdomain] = updated
            else:
                logger.info(f"Creating new DNS record for {subdomain}.{self.domain}")
                new_record = self.cf.dns.records.create(
                    zone_id=self.zone_id,
                    **record_data
                )
                self.dns_record_cache[subdomain] = new_record

            # Then update tunnel configuration
            self.update_tunnel_config(labels)

        except Exception as e:
            logger.error(f"Error updating DNS record for {subdomain}: {str(e)}")
            raise

    def handle_container_update(self, labels):
        """Handle both DNS and tunnel configuration updates for a container."""
        try:
            # First update DNS record and tunnel config
            self.update_dns_record(labels)
            self.update_tunnel_config(labels)
            
            # Then push tunnel configuration
            self.push_tunnel_config()

        except Exception as e:
            logger.error(f"Error handling container update for {labels.get('subdomain')}: {str(e)}")
            raise
