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


def get_kafka_topic_partitions() -> int:
    """Partition count for ensure_topics(). Local default 1; raise in production."""
    return int(os.environ.get("KAFKA_TOPIC_PARTITIONS", "1"))


def get_kafka_topic_replication_factor() -> int:
    """Replication factor for ensure_topics(). Local default 1; production >= 3."""
    return int(os.environ.get("KAFKA_TOPIC_REPLICATION_FACTOR", "1"))
