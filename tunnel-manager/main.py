import os
import logging
from logging.handlers import RotatingFileHandler
from time import monotonic, sleep
from cloudflare_manager import CloudflareManager
from docker_manager import DockerManager

LOG_DIR = '/app/logs'
LOG_FILE = f'{LOG_DIR}/cloudflared-tunnel-manager.log'

# How often to run a full reconcile. The event path handles normal changes in
# milliseconds; this is only the repair path for failed writes, external edits,
# and downtime, so it does not need to be frequent.
RECONCILE_INTERVAL = max(10, int(os.getenv('RECONCILE_INTERVAL') or '300'))

# Floor between reconcile passes, so a stream that closes early cannot turn the
# loop into a hot loop against the Cloudflare API.
MIN_RECONCILE_INTERVAL = 5

# Configure logging once, on the root logger, so every module reports to both
# the console and the rotated file exactly once.
os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(
    level=getattr(logging, os.getenv('LOG_LEVEL', 'INFO').upper()),
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.StreamHandler(),
        RotatingFileHandler(LOG_FILE, maxBytes=5_000_000, backupCount=3),
    ],
)
logger = logging.getLogger(__name__)


def reconcile(cf_manager: CloudflareManager, docker_manager: DockerManager) -> bool:
    """Re-read Docker state and converge Cloudflare to it."""
    try:
        return cf_manager.reconcile(docker_manager.get_desired_state())
    except Exception as e:
        # Keep the event loop alive; the next tick retries.
        logger.error(f"Reconcile failed: {str(e)}")
        return False


def main():
    # Get environment variables
    required_vars = {
        'CF_API_TOKEN': os.getenv('CF_API_TOKEN'),
        'CF_ACCOUNT_ID': os.getenv('CF_ACCOUNT_ID'),
        'TUNNEL_TOKEN': os.getenv('TUNNEL_TOKEN'),
        'CF_ZONE_ID': os.getenv('CF_ZONE_ID'),
        'DOMAIN': os.getenv('DOMAIN'),
    }

    # Check for missing variables
    missing_vars = [k for k, v in required_vars.items() if not v]
    if missing_vars:
        logger.error(f"Missing required environment variables: {', '.join(missing_vars)}")
        raise ValueError("Missing required environment variables")

    # Initialize managers
    cf_manager = CloudflareManager(
        api_token=required_vars['CF_API_TOKEN'],
        account_id=required_vars['CF_ACCOUNT_ID'],
        tunnel_token=required_vars['TUNNEL_TOKEN'],
        zone_id=required_vars['CF_ZONE_ID'],
        domain=required_vars['DOMAIN'],
        host_ip=os.getenv('HOST_IP', 'localhost')
    )
    docker_manager = DockerManager()

    logger.info("Starting DNS Manager...")
    reconcile(cf_manager, docker_manager)

    # Watch events for a while, then reconcile. The periodic reconcile is what
    # repairs state after missed events or failed API calls.
    logger.info("Starting container event monitoring")
    cursor = None
    while True:
        started = monotonic()
        cursor = docker_manager.watch_events(
            cf_manager.handle_container_update,
            timeout=RECONCILE_INTERVAL,
            since=cursor,
        )
        # Observable signal that the stream really does return on schedule.
        logger.debug("Event stream returned, running reconcile")
        reconcile(cf_manager, docker_manager)
        elapsed = monotonic() - started
        if elapsed < MIN_RECONCILE_INTERVAL:
            sleep(MIN_RECONCILE_INTERVAL - elapsed)


if __name__ == '__main__':
    while True:
        try:
            main()
        except Exception as e:
            logger.critical(f"Critical error in main loop: {str(e)}")
            logger.info("Restarting in 5 seconds...")
            sleep(5)
