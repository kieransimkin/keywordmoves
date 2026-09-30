class KeywordMovesError(Exception):
    """Base error shown cleanly by the CLI."""


class ConfigurationError(KeywordMovesError):
    """Required configuration is absent or invalid."""


class PluginNotFoundError(KeywordMovesError):
    """A named keyword or LLM plugin is unavailable."""


class InputError(KeywordMovesError):
    """Input data cannot be read or interpreted safely."""

