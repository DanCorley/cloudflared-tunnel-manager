import os
import logging
from time import sleep
from cloudflare_manager import CloudflareManager
from docker_manager import DockerManager

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger('dns-manager')

# Ensure logs directory exists
os.makedirs('/app/logs', exist_ok=True)

# Add file handler with rotation
log_file = '/app/logs/dns-manager.log'
log_handler = logging.FileHandler(log_file)
log_handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))
logger.addHandler(log_handler)

# Add console handler for container logs
console_handler = logging.StreamHandler()
console_handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))
logger.addHandler(console_handler)

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
    
    # Initial setup - cache DNS records and process existing containers
    try:
        # First get all existing DNS records and tunnel config
        cf_manager.get_dns_records()
        cf_manager.get_tunnel_config()
        
        # Process existing containers
        containers = docker_manager.get_running_containers()
        logger.info(f"Found {len(containers)} running containers")

        # Process DNS records and cache tunnel config updates
        for container in containers:
            logger.debug(f"Processing existing container: {container.name}")
            labels = docker_manager.get_container_labels(container)
            if labels:
                cf_manager.update_dns_record(labels)
                cf_manager.update_tunnel_config(labels)

        # Send the final tunnel configuration update
        logger.info("Pushing final tunnel configuration")
        cf_manager.push_tunnel_config()

    except Exception as e:
        logger.error(f"Error during initial setup: {str(e)}")
        raise

    # Watch for container events
    logger.info("Starting container event monitoring")
    docker_manager.watch_events(cf_manager.handle_container_update)


if __name__ == '__main__':
    while True:
        try:
            main()
        except Exception as e:
            logger.critical(f"Critical error in main loop: {str(e)}")
            logger.info("Restarting in 5 seconds...")
            sleep(5)
