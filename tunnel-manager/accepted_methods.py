from cloudflare import Cloudflare
from dotenv import load_dotenv
from cloudflare.types.shared.cloudflare_tunnel import CloudflareTunnel
from cloudflare.pagination import SyncV4PagePaginationArray
from cloudflare.types.dns.record_response import CNAMERecord
from cloudflare.types.zero_trust.tunnels.configuration_get_response import (
   ConfigurationGetResponse,
   Config,
)
from cloudflare.types.zero_trust.tunnels.configuration_update_response import (
   ConfigurationUpdateResponse,
)
from cloudflare.types.zero_trust.tunnels.configuration_update_response import (
   ConfigIngress
)

import os

load_dotenv()

# need to pull all of these from env variable
API_TOKEN = os.getenv('API_TOKEN')
ACCOUNT_ID = os.getenv('ACCOUNT_ID')
ZONE_ID = os.getenv('ZONE_ID')
TUNNEL_ID = os.getenv('TUNNEL_ID')
CF_TUNNEL_TOKEN = os.getenv('CF_TUNNEL_TOKEN')


cf = Cloudflare(
  api_token=API_TOKEN
)


def get_tunnel_attributes() -> CloudflareTunnel:

  # get attributes of tunnel
  return cf.zero_trust.tunnels.get(
    account_id=ACCOUNT_ID,
    tunnel_id=TUNNEL_ID
  )


def get_tunnel_configuration() -> ConfigurationGetResponse:
    return cf.zero_trust.tunnels.configurations.get(
      account_id=ACCOUNT_ID,
      tunnel_id=TUNNEL_ID
    )

def update_tunnel_configuration(config: Config) -> ConfigurationUpdateResponse:

  return cf.zero_trust.tunnels.configurations.update(
    account_id=ACCOUNT_ID,
    tunnel_id=TUNNEL_ID,
    config=config
  )

def build_tunnel_configuration(labels: dict, ingress: ConfigurationGetResponse) -> ConfigurationGetResponse:
  # Example values for the required fields
  hostname = f"{labels['subdomain']}.watermancorley.com"
  service = "http://192.168.68.69"
  path = None

  # Create the ConfigIngress object
  config_ingress = ConfigIngress(
      hostname=hostname,
      service=service,
      origin_request=None,
      path=path
  )
  ingress.config.ingress.insert(-2, config_ingress)
  return ingress

def list_dns_records(search: str) -> SyncV4PagePaginationArray[CNAMERecord]:
   return cf.dns.records.list(
      zone_id=ZONE_ID,
      type='CNAME',
      search=search
    )


def create_dns_record(name: str):
    return cf.dns.records.create(
      zone_id=ZONE_ID,
      comment='updated via dns-manager',
      content=f'{TUNNEL_ID}.cfargotunnel.com',
      name=name,
      proxied=True,
      ttl=1,
      type='CNAME'
    )

def update_dns_record(record_id: str, name: str):
    return cf.dns.records.edit(
      zone_id=ZONE_ID,
      dns_record_id=record_id,
      comment='updated via dns-manager',
      content=f'{TUNNEL_ID}.cfargotunnel.com',
      name=name,
      proxied=True,
      ttl=1,
      type='CNAME',
    )


def delete_dns_record(record_id: str):
    return cf.dns.records.delete(
      zone_id=ZONE_ID,
      dns_record_id=record_id,
    )
