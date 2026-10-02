"""Kafka adapter for `EventPort` (aiokafka, imported only here). PoC-4."""

from chassis.adapters.kafka.events import KafkaEvents

__all__ = ["KafkaEvents"]
