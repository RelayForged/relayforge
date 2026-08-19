"""
Kafka connection configuration for the messaging layer.

Mirrors relay/infrastructure/persistence/config.py: read from an
environment variable, default to the local docker-compose.yml Kafka
service, so no code changes are needed for local dev.
"""
import os

DEFAULT_KAFKA_BOOTSTRAP_SERVERS = "localhost:9092"


def get_kafka_bootstrap_servers() -> str:
    """Host-facing PLAINTEXT_HOST listener (see docker-compose.yml)."""
    return os.environ.get("KAFKA_BOOTSTRAP_SERVERS", DEFAULT_KAFKA_BOOTSTRAP_SERVERS)
