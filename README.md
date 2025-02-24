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
  - API token with DNS edit permissions
  - Cloudflare Tunnel created
  - Zero Trust access

## Environment Variables

Create a `.env` file with the following variables:

```env
# Cloudflare configuration
DOMAIN=your-domain.com
HOST_IP=your.local.ip.address

# Cloudflare API configuration
TUNNEL_TOKEN=your-tunnel-token
CF_API_TOKEN=your-api-token
CF_ACCOUNT_ID=your-account-id
CF_ZONE_ID=your-zone-id

# Optional: GitHub configuration for using published image
GITHUB_REPOSITORY=your-username/cloudflared
```

## Installation

### Using Pre-built Image

The easiest way to get started is using our pre-built image from GitHub Container Registry:

1. Clone this repository:
   ```bash
   git clone https://github.com/yourusername/cloudflared.git
   cd cloudflared
   ```

2. Set up your environment variables in `.env`

3. Start the services:
   ```bash
   docker compose up -d
   ```

### Building Locally

If you prefer to build the image locally:

1. Clone the repository as above

2. Modify the `docker-compose.yml` to build locally:
   ```yaml
   dns-manager:
     build:
       context: ./dns-manager
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
├── dns-manager/
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

### Publishing New Versions

To publish a new version of the DNS manager:

1. Create and push a new tag:
   ```bash
   git tag -a v1.0.0 -m "Release v1.0.0"
   git push origin v1.0.0
   ```

2. The GitHub Action will automatically:
   - Build the Docker image
   - Tag it with the version number
   - Push it to GitHub Container Registry

The image will be available at:
```
ghcr.io/your-username/cloudflared/dns-manager:v1.0.0
ghcr.io/your-username/cloudflared/dns-manager:latest
``` 