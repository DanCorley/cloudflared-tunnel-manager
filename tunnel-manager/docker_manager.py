from typing import Callable
import docker
import logging
import json

logger = logging.getLogger('dns-manager')

class DockerManager:
    def __init__(self):
        self.client = docker.DockerClient(base_url='unix://var/run/docker.sock')
        logger.info("Successfully initialized Docker client")

    def get_container_labels(self, container_or_event) -> dict:
        """Extract Cloudflare-related labels from container or event data.
        
        Args:
            container_or_event: Either a docker.models.containers.Container object
                              or a dictionary containing event data
        """
        try:
            # Handle event data (especially for 'die' events)
            if isinstance(container_or_event, dict):
                if 'Actor' not in container_or_event:
                    logger.error("Invalid event data: missing Actor field")
                    return None
                    
                labels = container_or_event['Actor'].get('Attributes', {})
                container_name = labels.get('name', 'unknown')
            else:
                # Handle container object
                labels = container_or_event.labels
                container_name = container_or_event.name

            # Get all labels prefixed with cloudflare
            cloudflare_labels = {
                key.replace('cloudflare.', ''): value
                for key, value in labels.items()
                if key.startswith('cloudflare.')
            }

            # Set enabled state based on cloudflare.enabled label to boolean
            cloudflare_labels['enabled'] = labels.get('cloudflare.enabled', '').lower() == 'true'
            
            # Ensure other required labels exist for enabled containers
            if cloudflare_labels['enabled']:
                if not cloudflare_labels.get('subdomain'):
                    cloudflare_labels['subdomain'] = container_name

                # Get port from container labels or event data
                if not cloudflare_labels.get('port'):
                    if isinstance(container_or_event, dict):
                        # For events, we can't get port info, use default
                        cloudflare_labels['port'] = '80'
                    elif any(container_or_event.ports.values()):
                        cloudflare_labels['port'] = list(container_or_event.ports.values())[-1][0].get('HostPort')

            logger.debug(f"Found Cloudflare labels for container {container_name}: {cloudflare_labels}")
            return cloudflare_labels

        except Exception as e:
            logger.error(f"Error getting labels for container: {str(e)}")
            return None

    def get_running_containers(self):
        """Get list of all running containers."""
        try:
            return self.client.containers.list()
        except Exception as e:
            logger.error(f"Error getting running containers: {str(e)}")
            raise

    def get_container_by_id(self, container_id: str) -> docker.models.containers.Container:
        """Get container by ID."""
        try:
            return self.client.containers.get(container_id)
        except docker.errors.NotFound:
            logger.warning(f"Container {container_id} not found")
            return None
        except Exception as e:
            logger.error(f"Error getting container {container_id}: {str(e)}")
            raise

    def handle_container_event(self, event: dict, callback: Callable):
        """Handle Docker container events."""
        try:
            action = event['Action']
            
            # For 'die' events, use the event data directly
            if action == 'die':
                labels = self.get_container_labels(event)
                container_name = event.get('Actor', {}).get('Attributes', {}).get('name', 'unknown')
            else:
                # For other events (like 'start'), try to get the container
                container = self.get_container_by_id(event['id'])
                if not container:
                    return
                container_name = container.name
                labels = self.get_container_labels(container)

            if not labels:
                return

            logger.info(f"Processing {action} event for container: {container_name}")
            logger.debug(f"Event details: {json.dumps(event)}")

            if labels.get('enabled', False):
                callback(labels, action)
            else:
                logger.debug(f"Container {container_name} has no valid Cloudflare labels")
                    
        except Exception as e:
            logger.error(f"Error handling container event: {str(e)}")

    def watch_events(self, callback: Callable):
        """Watch for container events and call callback when they occur."""
        try:
            for event in self.client.events(decode=True, filters={'Type': 'container'}):
                if event['Action'] in ['start', 'die']:
                    self.handle_container_event(event, callback)
        except Exception as e:
            logger.error(f"Error watching container events: {str(e)}")
            raise
