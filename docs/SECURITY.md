# CIRISLens Security Configuration

## Storing Agent Service Tokens

CIRISLens uses environment variables to securely store agent service tokens. This keeps sensitive credentials out of the codebase and allows for different tokens in different environments.

### Setup Instructions

1. **Copy the example environment file:**
   ```bash
   cp .env.example .env
   ```

2. **Edit `.env` and add your agent tokens:**
   ```bash
   # Agent Service Tokens
   AGENT_DATUM_TOKEN=service:YOUR_TOKEN_HERE
   AGENT_DATUM_URL=https://agents.ciris.ai/api/datum
   
   # Add more agents as needed
   AGENT_NEXUS_TOKEN=service:your-nexus-token-here
   AGENT_NEXUS_URL=https://agents.ciris.ai/api/nexus
   ```

3. **Ensure `.env` is never committed:**
   - The `.gitignore` file already excludes `.env`
   - Never commit tokens to version control

### Token Format

Agent tokens follow this naming convention:
- `AGENT_<NAME>_TOKEN` - The authentication token
- `AGENT_<NAME>_URL` - The base URL for the agent's API

Where `<NAME>` is the uppercase agent name (e.g., DATUM, NEXUS, PRISM).

### Using Tokens in Production

For production deployments, use proper secret management:

#### Docker Secrets
```yaml
secrets:
  datum_token:
    external: true
    
services:
  cirislens-api:
    secrets:
      - datum_token
    environment:
      AGENT_DATUM_TOKEN_FILE: /run/secrets/datum_token
```

#### Kubernetes Secrets
```yaml
apiVersion: v1
kind: Secret
metadata:
  name: agent-tokens
type: Opaque
data:
  datum_token: <base64-encoded-token>
```

#### Cloud Provider Secrets
- **AWS**: Use AWS Secrets Manager or Parameter Store
- **GCP**: Use Google Secret Manager
- **Azure**: Use Azure Key Vault

### OTLP Collection

The OTLP collector automatically discovers configured agents from environment variables and collects:
- **Metrics**: System and service-level metrics
- **Traces**: Cognitive processing traces
- **Logs**: Audit and system logs

Collection happens every 30 seconds by default (configurable via `COLLECTION_INTERVAL_SECONDS`).

### Security Best Practices

1. **Rotate tokens regularly** - Generate new service tokens periodically
2. **Use unique tokens per agent** - Don't share tokens between agents
3. **Restrict token permissions** - Use read-only tokens for telemetry collection
4. **Monitor token usage** - Track which tokens are accessing telemetry
5. **Use HTTPS only** - Always use encrypted connections
6. **Implement rate limiting** - Prevent token abuse

### Token Validation

CIRISLens validates tokens on each collection cycle. Failed authentications are logged and stored in the `collection_errors` table.

To check token validity:
```bash
# Test Datum token
curl -H "Authorization: Bearer service:YOUR_TOKEN_HERE" \
  https://agents.ciris.ai/api/datum/v1/telemetry/otlp/metrics
```

### Troubleshooting

If telemetry collection fails:

1. **Check token format:**
   - Must include `service:` prefix for service tokens
   - No extra spaces or newlines

2. **Verify environment variables:**
   ```bash
   docker exec cirislens-api env | grep AGENT_
   ```

3. **Check collection logs:**
   ```bash
   docker logs cirislens-api | grep OTLP
   ```

4. **Query error table:**
   ```sql
   SELECT * FROM collection_errors 
   WHERE agent_name = 'datum' 
   ORDER BY occurred_at DESC 
   LIMIT 10;
   ```

### OAuth Configuration

For the admin interface, configure Google OAuth:

1. Create OAuth 2.0 credentials in Google Cloud Console
2. Set authorized redirect URI to: `https://your-domain/cirislens/api/admin/auth/callback`
3. Add to `.env`:
   ```bash
   GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com
   GOOGLE_CLIENT_SECRET=your-client-secret
   ALLOWED_DOMAIN=ciris.ai
   ```

### Database Security

The PostgreSQL database uses these security measures:
- Separate database user with limited permissions
- Connection via internal Docker network only
- No external port exposure in production
- Regular backups to encrypted storage

### Network Security

Production deployment should include:
- TLS/SSL certificates (Let's Encrypt recommended)
- Firewall rules restricting access
- VPN or private network for internal services
- Rate limiting on all public endpoints
- DDoS protection (e.g., Cloudflare)

## Incident Response

If credentials are compromised:

1. **Immediately rotate affected tokens**
2. **Check access logs for unauthorized use**
3. **Update all deployment environments**
4. **Notify affected agent owners**
5. **Review and update security practices**

## Compliance

CIRISLens follows these compliance principles:
- **No PII storage** - Personal data is never collected
- **Audit logging** - All access is logged
- **Data retention** - Telemetry data expires after configured period
- **Access control** - Role-based permissions for admin interface
- **Encryption** - TLS in transit, encrypted storage at rest