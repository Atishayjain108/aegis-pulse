"""Lake catalog: table registry, partitions, and lineage."""

from aegis.datalake.catalog.registry import (
    LakeCatalog,
    LineageEdge,
    PartitionInfo,
    TableInfo,
)

__all__ = ["LakeCatalog", "LineageEdge", "PartitionInfo", "TableInfo"]
