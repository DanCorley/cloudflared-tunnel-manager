from typing import Callable
import docker
import logging
import json

logger = logging.getLogger('dns-manager')

class DockerManager:
    def __init__(self):
        self.client = docker.DockerClient(base_url='unix://var/run/docker.sock')
        logger.info("Successfully initialized Docker client")

    def get_container_labels(self, container: docker.models.containers.Container) -> dict:
        """Extract Cloudflare-related labels from container."""
        try:
            labels = container.labels

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
                    cloudflare_labels['subdomain'] = container.name

                # Get port from container labels or ports if any exposed
                if not cloudflare_labels.get('port') and any(container.ports.values()):
                    cloudflare_labels['port'] = list(container.ports.values())[-1][0].get('HostPort')

            logger.debug(f"Found Cloudflare labels for container {container.name}: {cloudflare_labels}")
            return cloudflare_labels

        except Exception as e:
            logger.error(f"Error getting labels for container {container.name}: {str(e)}")
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
            container = self.get_container_by_id(event['id'])
            
            if not container:
                return

            logger.info(f"Processing {action} event for container: {container.name}")
            logger.debug(f"Event details: {json.dumps(event)}")

            labels = self.get_container_labels(container)
            if labels:
                # Set enabled state based on container action
                labels['enabled'] = str(action == 'start').lower()
                callback(labels, action)
            else:
                logger.debug(f"Container {container.name} has no valid Cloudflare labels")
                    
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
