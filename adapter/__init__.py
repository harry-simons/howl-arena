"""Model-aware player adapter.

This is the ONLY layer that knows about models and providers. It turns an
engine PlayerView into a prompt, calls a provider, and parses the reply back
into a validated Action. The engine and runner stay model-agnostic; nothing
provider-specific leaks out of this package.
"""
