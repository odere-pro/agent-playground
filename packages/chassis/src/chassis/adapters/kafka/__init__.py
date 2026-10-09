"""Kafka adapter for `EventPort` (aiokafka, imported only here). PoC-4."""

from chassis.adapters.kafka.events import KafkaEvents, SaslCredentials

__all__ = ["KafkaEvents", "SaslCredentials"]
