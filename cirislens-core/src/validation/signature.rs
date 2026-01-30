//! Ed25519 signature verification.
//!
//! Verifies trace signatures using public keys loaded from database.

use std::collections::HashMap;
use std::sync::RwLock;
use std::time::{Duration, Instant};

use base64::{engine::general_purpose, Engine as _};
use ed25519_dalek::{Signature, VerifyingKey, Verifier};
use lazy_static::lazy_static;
use sha2::{Digest, Sha256};

use crate::logging::structured::LogContext;

/// Cache TTL - 5 minutes
const KEY_CACHE_TTL_SECS: u64 = 300;

/// Signature verification result.
#[derive(Debug)]
pub struct SignatureVerificationResult {
    pub verified: bool,
    pub key_id: Option<String>,
    pub error: Option<String>,
}

impl SignatureVerificationResult {
    pub fn verified(key_id: &str) -> Self {
        Self {
            verified: true,
            key_id: Some(key_id.to_string()),
            error: None,
        }
    }

    pub fn no_signature() -> Self {
        Self {
            verified: false,
            key_id: None,
            error: Some("No signature provided".to_string()),
        }
    }

    pub fn unknown_key(key_id: &str) -> Self {
        Self {
            verified: false,
            key_id: Some(key_id.to_string()),
            error: Some("Unknown signer key".to_string()),
        }
    }

    pub fn invalid(key_id: &str, error: &str) -> Self {
        Self {
            verified: false,
            key_id: Some(key_id.to_string()),
            error: Some(error.to_string()),
        }
    }
}

/// Cache for public keys.
#[derive(Debug)]
pub struct PublicKeyCache {
    keys: HashMap<String, VerifyingKey>,
    loaded_at: Option<Instant>,
}

impl Default for PublicKeyCache {
    fn default() -> Self {
        Self {
            keys: HashMap::new(),
            loaded_at: None,
        }
    }
}

impl PublicKeyCache {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn is_empty(&self) -> bool {
        self.keys.is_empty()
    }

    pub fn key_count(&self) -> usize {
        self.keys.len()
    }

    /// Check if cache needs refresh (empty or TTL expired).
    pub fn needs_refresh(&self) -> bool {
        if self.keys.is_empty() {
            return true;
        }
        match self.loaded_at {
            Some(loaded_at) => loaded_at.elapsed() > Duration::from_secs(KEY_CACHE_TTL_SECS),
            None => true,
        }
    }

    /// Get cache age in seconds (for logging).
    pub fn cache_age_secs(&self) -> Option<u64> {
        self.loaded_at.map(|t| t.elapsed().as_secs())
    }

    /// Mark cache as freshly loaded.
    pub fn mark_loaded(&mut self) {
        self.loaded_at = Some(Instant::now());
    }

    pub fn has_key(&self, key_id: &str) -> bool {
        self.keys.contains_key(key_id)
    }

    pub fn get_key(&self, key_id: &str) -> Option<&VerifyingKey> {
        self.keys.get(key_id)
    }

    /// Load public key from base64-encoded bytes.
    pub fn load_key(&mut self, key_id: &str, public_key_base64: &str) -> Result<(), String> {
        let key_bytes = general_purpose::STANDARD
            .decode(public_key_base64)
            .map_err(|e| format!("Failed to decode base64: {}", e))?;

        if key_bytes.len() != 32 {
            return Err(format!(
                "Invalid key length: expected 32, got {}",
                key_bytes.len()
            ));
        }

        let key_array: [u8; 32] = key_bytes
            .try_into()
            .map_err(|_| "Failed to convert key bytes")?;

        let verifying_key = VerifyingKey::from_bytes(&key_array)
            .map_err(|e| format!("Invalid public key: {}", e))?;

        self.keys.insert(key_id.to_string(), verifying_key);
        Ok(())
    }

    /// Clear all keys.
    pub fn clear(&mut self) {
        self.keys.clear();
        self.loaded_at = None;
        log::info!("PUBLIC_KEY_CACHE_CLEARED");
    }
}

lazy_static! {
    static ref PUBLIC_KEY_CACHE: RwLock<PublicKeyCache> = RwLock::new(PublicKeyCache::new());
}

/// Get a read-only reference to the public key cache.
pub fn get_key_cache() -> std::sync::RwLockReadGuard<'static, PublicKeyCache> {
    PUBLIC_KEY_CACHE.read().expect("Key cache lock poisoned")
}

/// Get a mutable reference to the public key cache.
pub fn get_key_cache_mut() -> std::sync::RwLockWriteGuard<'static, PublicKeyCache> {
    PUBLIC_KEY_CACHE.write().expect("Key cache lock poisoned")
}

/// Verify an Ed25519 signature.
///
/// # Arguments
/// * `message` - The message that was signed (canonical JSON)
/// * `signature_base64` - Base64-encoded signature
/// * `key_id` - ID of the signing key
/// * `ctx` - Logging context
pub fn verify_signature(
    message: &str,
    signature_base64: &str,
    key_id: &str,
    ctx: &LogContext,
) -> SignatureVerificationResult {
    let cache = get_key_cache();

    // Check if we have any keys loaded
    if cache.is_empty() {
        log::warn!(
            "{} SIGNATURE_SKIP reason=no_keys_loaded key_id={}",
            ctx,
            key_id
        );
        // No keys = accept without verification (unverified mode)
        return SignatureVerificationResult {
            verified: false,
            key_id: Some(key_id.to_string()),
            error: Some("No public keys loaded - unverified mode".to_string()),
        };
    }

    // Look up the key
    let verifying_key = match cache.get_key(key_id) {
        Some(key) => key,
        None => {
            log::warn!(
                "{} SIGNATURE_KEY_LOOKUP key_id={} found=false",
                ctx,
                key_id
            );
            return SignatureVerificationResult::unknown_key(key_id);
        }
    };

    log::debug!(
        "{} SIGNATURE_KEY_LOOKUP key_id={} found=true",
        ctx,
        key_id
    );

    // Decode signature (try URL-safe first, then standard base64)
    let signature_bytes = general_purpose::URL_SAFE_NO_PAD
        .decode(signature_base64)
        .or_else(|_| general_purpose::STANDARD.decode(signature_base64));

    let signature_bytes = match signature_bytes {
        Ok(bytes) => bytes,
        Err(e) => {
            log::warn!(
                "{} SIGNATURE_DECODE_FAILED key_id={} error={}",
                ctx,
                key_id,
                e
            );
            return SignatureVerificationResult::invalid(key_id, &format!("Decode error: {}", e));
        }
    };

    log::debug!(
        "{} SIGNATURE_DECODE success=true key_id={}",
        ctx,
        key_id
    );

    // Parse signature
    let signature = match Signature::from_slice(&signature_bytes) {
        Ok(sig) => sig,
        Err(e) => {
            log::warn!(
                "{} SIGNATURE_PARSE_FAILED key_id={} error={}",
                ctx,
                key_id,
                e
            );
            return SignatureVerificationResult::invalid(key_id, &format!("Parse error: {}", e));
        }
    };

    // Verify
    match verifying_key.verify(message.as_bytes(), &signature) {
        Ok(()) => {
            log::info!(
                "{} SIGNATURE_VERIFY key_id={} valid=true",
                ctx,
                key_id
            );
            SignatureVerificationResult::verified(key_id)
        }
        Err(e) => {
            log::warn!(
                "{} SIGNATURE_INVALID key_id={} error={}",
                ctx,
                key_id,
                e
            );
            SignatureVerificationResult::invalid(key_id, &format!("Verification failed: {}", e))
        }
    }
}

/// Compute SHA256 hash of content.
pub fn compute_hash(content: &str) -> String {
    let mut hasher = Sha256::new();
    hasher.update(content.as_bytes());
    let result = hasher.finalize();
    hex::encode(result)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_compute_hash() {
        let hash = compute_hash("test content");
        assert_eq!(hash.len(), 64); // SHA256 produces 64 hex chars
    }

    #[test]
    fn test_key_cache() {
        let mut cache = PublicKeyCache::new();
        assert!(cache.is_empty());

        // This is a valid test Ed25519 public key (32 bytes, base64 encoded)
        // In production, real keys would be loaded from database
        let test_key = "11111111111111111111111111111111111111111111";

        // Key loading will fail with invalid key, but cache operations should work
        let result = cache.load_key("test-key", test_key);
        // This specific key is invalid, so it should fail
        assert!(result.is_err());

        assert!(!cache.has_key("test-key"));
    }
}
