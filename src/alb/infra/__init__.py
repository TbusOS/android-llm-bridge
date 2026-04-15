"""Infrastructure layer.

Cross-cutting concerns shared by all other layers:
- result     : unified Result[T] return type
- errors     : error code catalog
- permissions: permission engine (blocklist + policy)
- registry   : metadata-driven transport / capability registry
- workspace  : path conventions for artifacts
- config     : config.toml + profile loading
- events     : in-process pub/sub
- memory     : session memory (M3)

See docs/architecture.md §4 for the full picture.
"""
