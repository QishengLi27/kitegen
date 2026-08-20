# Aliyun Lightweight Server Deployment Guide (kitegen demo)

## 1. Purchase a server

- Product: Alibaba Cloud Lightweight Application Server
- Image: **Application Image → Docker**
- Spec: 2 cores / 2 GB minimum (enough for uvicorn + monitor + paper trader)
- Region: Mainland China node (faster access to Tencent quote API)
- Bandwidth: choose per package; SSE streaming needs stable bandwidth
- OS: usually pre-installed Ubuntu 22.04 / CentOS + Docker

## 2. Security group / firewall

Open these ports in the Aliyun console:

| Port | Purpose |
|------|---------|
| 22   | SSH |
| 80   | HTTP (Nginx) |
| 443  | HTTPS (optional) |

Port 8000 is **not exposed publicly** anymore; the backend is only reachable inside the Docker network via Nginx.

## 3. Prepare locally

Run these from the project root:

```bash
# 1. Make sure the frontend builds
npm ci --prefix demo/frontend
npm run build --prefix demo/frontend

# 2. Upload to the server (replace <SERVER_IP>)
scp -r Dockerfile docker-compose.yml nginx.conf pyproject.toml README.md src demo root@<SERVER_IP>:/opt/kitegen/
```

> Alternatively, push to GitHub and `git clone` on the server.

## 4. Start on the server

```bash
ssh root@<SERVER_IP>
cd /opt/kitegen

# Create environment file
cat > .env <<'EOF'
LLM_API_KEY=sk-your-key-here
LLM_API_BASE=https://api.deepseek.com/v1
LLM_MODEL=deepseek-v4-flash
RISK_MODE=normal
MONITOR_INTERVAL=300
MONITOR_MOVE_THRESHOLD=5
MONITOR_BRIEFING_TIME=08:50
# WEBHOOK_URL=
EOF

# Pull images and start in the background
docker compose up -d

# View logs
docker compose logs -f
```

## 5. Verify

```bash
# Health check through Nginx
curl http://<SERVER_IP>/health

# Open the web UI
open http://<SERVER_IP>
```

If you see the frontend page, deployment is successful.

## 6. HTTPS (optional but recommended)

Apply a free Let's Encrypt certificate on the server:

```bash
docker run -it --rm \
  -v /opt/kitegen/cert:/etc/letsencrypt \
  -v /opt/kitegen/cert-www:/var/www/certbot \
  certbot/certbot certonly --standalone -d your-domain.com
```

Then uncomment the 443 server block in `nginx.conf` and set the certificate paths:

```nginx
ssl_certificate /etc/nginx/cert/live/your-domain.com/fullchain.pem;
ssl_certificate_key /etc/nginx/cert/live/your-domain.com/privkey.pem;
```

Mount the certificate directory into the Nginx container:

```yaml
volumes:
  - ./cert:/etc/nginx/cert:ro
```

Restart:

```bash
docker compose down
docker compose up -d
```

## 7. Data persistence

All user data is written under `demo/data/`:

```
demo/data/
├── default.json          # portfolio positions
├── alerts.json           # monitor alerts
├── usage.json            # LLM usage
├── monitor_state.json    # monitor state
└── paper/
    ├── account.json
    ├── config.json
    └── trades.json
```

`docker-compose.yml` already mounts this directory to the host, so data survives container recreation.

## 8. Update code

```bash
cd /opt/kitegen
git pull   # or re-upload via scp

# Rebuild and restart
docker compose down
docker compose up --build -d
```

## 9. Security reminder

This demo currently has **no authentication**. If exposed to the public internet:
- Anyone can view your positions, call the LLM, and operate the paper trader
- Add access control (Nginx Basic Auth, IP whitelist, or Tailscale private network) before public use

---

## Quick checklist

- [ ] Lightweight server created with Docker application image
- [ ] Security group open for 22/80/443
- [ ] Code uploaded to `/opt/kitegen`
- [ ] `.env` created with LLM_API_KEY
- [ ] `docker compose up -d` started successfully
- [ ] `curl http://<IP>/health` returns ok
- [ ] Browser opens `http://<IP>` and shows the UI
- [ ] (Optional) HTTPS enabled
