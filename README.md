# Cloudflare DNS Manager

A Docker-based solution that automatically manages Cloudflare DNS records and tunnel configurations for your containers. This project makes it easy to expose your local services through Cloudflare Tunnels with automatic DNS record management.

## Features

- 🔄 Automatic DNS record management for Docker containers
- 🚇 Cloudflare Tunnel integration
- 🏷️ Simple container label-based configuration
- 🔒 Secure exposure of local services
- 📝 Comprehensive logging
- 🔌 Zero-trust network access

## Prerequisites

- Docker and Docker Compose
- A Cloudflare account with:
  - A registered domain
  - API token with edit / view permissions:
    - Account -> Cloudflare Tunnel
    - Zone -> DNS
  - Cloudflare Tunnel created
  - Zero Trust access

## Environment Variables

Create a `.env` file with the following variables:

```env
# Cloudflare configuration
DOMAIN=your-domain.com
HOST_IP=your.machine.ip.address # defaults to localhost

# Cloudflare API configuration
TUNNEL_TOKEN=your-tunnel-token
CF_API_TOKEN=your-api-token
CF_ACCOUNT_ID=your-account-id
CF_ZONE_ID=your-zone-id

# Optional
RECONCILE_INTERVAL=300  # seconds between full reconciles (default 300)
```

## Usage

### Container Labels

Add these labels to any container you want to expose through Cloudflare:

```yaml
services:
  nginx:
    image: nginx:latest
    ports:
      - 80:80
    labels:
      - "cloudflare.enabled=true"         # Required: Enable Cloudflare integration
      - "cloudflare.subdomain=hello"      # Creates hello.yourdomain.com
      - "cloudflare.port=80"              # Optional: Enables a specific port
```

The DNS manager will automatically:
- Create `hello.**yourdomain**.com` hostname on your Cloudflare tunnel
  - and create a dns record pointing to tunnel
- Remove these records when the container stops
- Update the record if labels change upon container re-creation
- Re-reconcile every 5 minutes by default (`RECONCILE_INTERVAL`), cleaning up
  anything left behind while the manager was down

> **Upgrading:** DNS records carry an ownership comment, but tunnel ingress
> rules do not. Any rule whose hostname is under `DOMAIN` and whose service is
> `http://${HOST_IP}:<port>` is treated as managed and will be pruned when no
> running container claims it. Back up your tunnel configuration first if you
> hand-manage rules that match that pattern.


## Installation

### Using Pre-built Image

The easiest way to get started is using our pre-built image from GitHub Container Registry:

1. Create a docker-compose file alongside cloudflared:
   ```yaml
   services:
   cloudflared:
      image: cloudflare/cloudflared:latest
      container_name: cloudflared
      restart: unless-stopped
      command: tunnel run
      volumes:
         - ./cloudflared:/etc/cloudflared
      environment:
         - TUNNEL_TOKEN=${TUNNEL_TOKEN}

   tunnel-manager:
      image: ghcr.io/dancorley/cloudflared-tunnel-manager:latest
      container_name: cloudflared-tunnel-manager
      restart: unless-stopped
      volumes:
         - /var/run/docker.sock:/var/run/docker.sock:ro
         - ./logs:/app/logs
      environment:
         - CF_API_TOKEN=${CF_API_TOKEN}
         - CF_ACCOUNT_ID=${CF_ACCOUNT_ID}
         - TUNNEL_TOKEN=${TUNNEL_TOKEN}
         - CF_ZONE_ID=${CF_ZONE_ID}
         - DOMAIN=${DOMAIN}
         - HOST_IP=${HOST_IP:-localhost}
   ```

2. Set up your environment variables in `.env`

3. Set labels like `cloudflare.enabled=true` on others + restart containers

4. Start the services with cloudflared-tunnel-manager:
   ```bash
   docker compose up -d
   ```
5. Restart other containers if labels not set.

### Building Locally

If you prefer to build the image locally:

1. Clone the repository as above

2. Modify the `docker-compose.yml` to build locally:
   ```yaml
   tunnel-manager:
     build:
       context: ./tunnel-manager
       dockerfile: Dockerfile
     # ... rest of configuration
   ```

3. Build and start the services:
   ```bash
   docker compose up -d --build
   ```

## Development

### Project Structure

```
.
├── docker-compose.yml          # Main compose file
├── .env                       # Environment variables
├── tunnel-manager/
│   ├── Dockerfile            # DNS manager container definition
│   ├── requirements.txt      # Python dependencies
│   ├── main.py              # Application entry point
│   ├── cloudflare_manager.py # Cloudflare API interactions
│   └── docker_manager.py     # Docker API interactions
├── .github/
│   └── workflows/            # GitHub Actions workflows
│       └── publish.yml       # Container publishing workflow
└── logs/                     # Log directory
```


### Best Practices

1. **Naming**
   - Use descriptive subdomains that reflect the service
   - Avoid using special characters in subdomains
   - Keep subdomains short and memorable

2. **Security**
   - Only enable Cloudflare integration for services that need external access
   - Consider using separate domains for internal and external services

3. **Organization**
   - Group related services under similar subdomain patterns
   - Use consistent naming conventions across your infrastructure
