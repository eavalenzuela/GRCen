# Configuring HTTPS

GRCen supports HTTPS in two ways:

1. **Nginx reverse proxy** (recommended for production) -- nginx terminates TLS and forwards traffic to the GRCen app over the internal Docker network.
2. **Direct TLS** (simpler, fewer moving parts) -- GRCen itself serves HTTPS using certificates you provide via environment variables.

Both methods enforce TLS 1.2+, set the `Strict-Transport-Security` (HSTS) header, and automatically redirect HTTP requests to HTTPS.

---

## Option 1: Nginx Reverse Proxy (Production)

This is the recommended setup for production. Nginx handles TLS termination, HTTP-to-HTTPS redirection, HSTS, OCSP stapling, and static file serving. The GRCen app runs on plain HTTP internally and is never exposed to the outside.

### Prerequisites

- A registered domain name pointed at your server's IP address
- Docker and Docker Compose installed
- TLS certificate and private key files (see "Obtaining Certificates" below)

### Step 1: Obtain TLS Certificates

**With Let's Encrypt (free, automated):**

```bash
# Install certbot
sudo apt install certbot    # Debian/Ubuntu
sudo dnf install certbot    # Fedora/RHEL

# Obtain a certificate (standalone mode — stop any service on port 80 first)
sudo certbot certonly --standalone -d grcen.example.com

# Certificates will be at:
#   /etc/letsencrypt/live/grcen.example.com/fullchain.pem
#   /etc/letsencrypt/live/grcen.example.com/privkey.pem
```

**With a commercial CA:**

Your CA will provide a certificate file (or chain) and a private key. Combine any intermediate certificates with your server certificate into a single `fullchain.pem` file.

### Step 2: Place Certificates

Copy your certificate and key into `deploy/ssl/` inside the GRCen directory:

```bash
mkdir -p deploy/ssl

# From Let's Encrypt:
sudo cp /etc/letsencrypt/live/grcen.example.com/fullchain.pem deploy/ssl/
sudo cp /etc/letsencrypt/live/grcen.example.com/privkey.pem deploy/ssl/

# Set ownership so Docker can read them
sudo chown $(id -u):$(id -g) deploy/ssl/*.pem
chmod 600 deploy/ssl/privkey.pem
```

The `deploy/ssl/` directory is gitignored -- your keys will never be committed.

### Step 3: Edit the Nginx Config

Open `deploy/nginx.conf` and replace `grcen.example.com` with your actual domain on these lines:

```
server_name grcen.example.com;    # line 16 (HTTP server)
server_name grcen.example.com;    # line 31 (HTTPS server)
```

The certificate paths in the config (`/etc/nginx/ssl/fullchain.pem` and `/etc/nginx/ssl/privkey.pem`) are already correct -- they map to the files you placed in `deploy/ssl/` via the Docker volume mount.

### Step 4: Set a Secret Key

Create a `.env` file in the project root (or export the variable) with a strong random secret:

```bash
echo "SECRET_KEY=$(openssl rand -hex 32)" > .env
```

The production compose file requires this and will refuse to start without it.

### Step 5: Start the Stack

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

This starts three containers:

| Container | Role | Ports |
|---|---|---|
| `db` | PostgreSQL database | 5432 (internal) |
| `app` | GRCen application | 8000 (internal only) |
| `nginx` | TLS reverse proxy | **80** (redirects to 443), **443** |

### Step 6: Verify

```bash
# Should return a 301 redirect to https://
curl -I http://grcen.example.com

# Should return 200 with security headers
curl -I https://grcen.example.com/health

# Check TLS configuration
openssl s_client -connect grcen.example.com:443 -tls1_2 </dev/null 2>/dev/null | grep -E "Protocol|Cipher"
```

Confirm that:
- HTTP requests redirect to HTTPS
- The `Strict-Transport-Security` header is present
- The TLS protocol is 1.2 or 1.3
- The health endpoint returns `{"status":"ok"}`

### Certificate Renewal

Let's Encrypt certificates expire every 90 days. Set up automatic renewal:

```bash
# Test renewal
sudo certbot renew --dry-run

# Add a cron job to renew and reload nginx
echo "0 3 * * * certbot renew --quiet && docker compose -f /path/to/docker-compose.yml -f /path/to/docker-compose.prod.yml exec nginx nginx -s reload" | sudo crontab -
```

If you use a different certificate source, replace the files in `deploy/ssl/` and restart the nginx container:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml restart nginx
```

### Architecture Diagram

```
Internet
   |
   v
[ nginx :443 ] --TLS termination--> [ app :8000 ] --> [ db :5432 ]
   |                                  (plain HTTP,
   +-- :80 redirects to :443          internal only)
```

---

## Option 2: Direct TLS (Simple Setup)

If you don't want to run nginx, GRCen can terminate TLS itself. This is simpler but doesn't give you nginx's static file serving, advanced load balancing, or OCSP stapling.

This works for:
- Small deployments with a few users
- Development/staging environments that need HTTPS (e.g. testing secure cookies or OIDC)
- Environments where another load balancer sits in front

### Prerequisites

- TLS certificate and private key files (PEM format)
- Docker and Docker Compose, **or** Python 3.12+ for local development

### With Docker Compose

#### Step 1: Place Certificates

Put your certificate and key somewhere accessible. For example, create a `certs/` directory:

```bash
mkdir -p certs
cp /path/to/fullchain.pem certs/
cp /path/to/privkey.pem certs/
chmod 600 certs/privkey.pem
```

#### Step 2: Configure Environment

Create or edit your `.env` file:

```bash
SECRET_KEY=your-random-secret-key-here
SSL_CERTFILE=/certs/fullchain.pem
SSL_KEYFILE=/certs/privkey.pem
```

#### Step 3: Update docker-compose.yml

Edit `docker-compose.yml` to mount the certificates and expose port 8443:

```yaml
services:
  app:
    build: .
    ports:
      - "8443:8443"       # HTTPS port
    environment:
      DATABASE_URL: postgresql://grcen:grcen@db:5432/grcen
      SECRET_KEY: "${SECRET_KEY:?Set SECRET_KEY in .env}"
      DEBUG: "false"
      UPLOAD_DIR: /app/uploads
      SSL_CERTFILE: /certs/fullchain.pem
      SSL_KEYFILE: /certs/privkey.pem
    volumes:
      - uploads:/app/uploads
      - ./certs:/certs:ro   # Mount certificates read-only
    depends_on:
      db:
        condition: service_healthy
```

#### Step 4: Start

```bash
docker compose up -d
```

GRCen is now available at `https://localhost:8443`. When `SSL_CERTFILE` and `SSL_KEYFILE` are set, the entrypoint automatically switches from port 8000 to 8443 and passes the certificates to Gunicorn.

#### Step 5: Verify

```bash
curl -k https://localhost:8443/health
# {"status":"ok"}

# If using a real domain with valid certs:
curl https://grcen.example.com:8443/health
```

### Local Development (Without Docker)

#### Step 1: Generate a Self-Signed Certificate (Development Only)

```bash
openssl req -x509 -newkey rsa:2048 -nodes \
    -keyout dev-key.pem -out dev-cert.pem \
    -days 365 -subj "/CN=localhost"
```

Your browser will show a security warning for self-signed certificates. This is expected and safe for local development.

#### Step 2: Set Environment Variables

Add to your `.env` file:

```bash
SSL_CERTFILE=dev-cert.pem
SSL_KEYFILE=dev-key.pem
DEBUG=true
```

Or export them directly:

```bash
export SSL_CERTFILE=dev-cert.pem
export SSL_KEYFILE=dev-key.pem
```

#### Step 3: Start the Server

```bash
grcen runserver
```

When TLS is configured, the dev server listens on port **8443** instead of 8000:

```
INFO:     Uvicorn running on https://0.0.0.0:8443
```

Open `https://localhost:8443` in your browser.

---

## How It Works Internally

When `SSL_CERTFILE` and `SSL_KEYFILE` are set:

- **`grcen runserver`** passes `ssl_certfile` and `ssl_keyfile` to Uvicorn and switches to port 8443.
- **Docker entrypoint** passes `--certfile` and `--keyfile` to Gunicorn and binds to 8443.
- **`HTTPSRedirectMiddleware`** is registered automatically when TLS is configured or `DEBUG=false`. It redirects any plain HTTP request to HTTPS (301) and sets the `Strict-Transport-Security` header on HTTPS responses. It respects the `X-Forwarded-Proto` header so it works correctly behind a reverse proxy without redirect loops.
- **Session cookies** are set with `Secure` flag when `DEBUG=false`, meaning they are only sent over HTTPS connections.

## Troubleshooting

**"This site can't provide a secure connection"**
- Verify the certificate files exist and are readable: `ls -la deploy/ssl/` or `ls -la certs/`
- Check that `fullchain.pem` includes the full chain (server cert + intermediates), not just the server cert

**Redirect loop**
- If running behind a reverse proxy, make sure it sets `X-Forwarded-Proto: https`. The GRCen middleware checks this header to avoid redirecting traffic that is already HTTPS at the proxy layer.
- The nginx config in `deploy/nginx.conf` sets this header automatically.

**Cookies not being sent (logged out on every request)**
- When `DEBUG=false`, session cookies require HTTPS (`Secure` flag). Make sure you are accessing the app via `https://`, not `http://`.
- For local development with self-signed certs, set `DEBUG=true` in your `.env` to disable the `Secure` flag.

**Certificate renewal didn't take effect**
- Nginx caches the certificate in memory. Reload it after renewal: `docker compose exec nginx nginx -s reload`
- For direct TLS, restart the app container: `docker compose restart app`

**Port 8443 instead of 443**
- Direct TLS uses port 8443 by default (a non-privileged port). To serve on 443, either:
  - Use the nginx setup (Option 1), which binds to 443 itself
  - Map the port in Docker: `ports: ["443:8443"]`
  - Run behind a cloud load balancer that terminates TLS on 443
