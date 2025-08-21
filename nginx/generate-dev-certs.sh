#!/bin/bash
# Generate self-signed certificates for development

CERT_DIR="./ssl"
CERT_FILE="$CERT_DIR/cert.pem"
KEY_FILE="$CERT_DIR/key.pem"

# Create SSL directory if it doesn't exist
mkdir -p "$CERT_DIR"

# Check if certificates already exist
if [ -f "$CERT_FILE" ] && [ -f "$KEY_FILE" ]; then
    echo "Certificates already exist in $CERT_DIR"
    echo "To regenerate, delete the existing files first."
    exit 0
fi

# Generate self-signed certificate
echo "Generating self-signed certificate for development..."
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
    -keyout "$KEY_FILE" \
    -out "$CERT_FILE" \
    -subj "/C=US/ST=State/L=City/O=CIRISLens Dev/CN=localhost" \
    -addext "subjectAltName=DNS:localhost,DNS:*.localhost,DNS:agents.ciris.ai,IP:127.0.0.1"

if [ $? -eq 0 ]; then
    echo "✅ Certificates generated successfully:"
    echo "   Certificate: $CERT_FILE"
    echo "   Private Key: $KEY_FILE"
    echo ""
    echo "⚠️  These are self-signed certificates for DEVELOPMENT ONLY."
    echo "   Your browser will show a security warning - this is expected."
    chmod 600 "$KEY_FILE"
    chmod 644 "$CERT_FILE"
else
    echo "❌ Failed to generate certificates"
    exit 1
fi