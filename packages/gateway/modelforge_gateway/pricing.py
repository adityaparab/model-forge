"""Per-model token pricing for cost accounting."""

from modelforge_gateway.profiles import Price
from modelforge_gateway.schemas import Usage


def cost_usd(usage: Usage, price: Price | None) -> float:
    """Actual spend for ``usage``; zero for an unpriced (local or fake) model."""
    if price is None:
        return 0.0
    uncached = max(usage.input_tokens - usage.cache_read_tokens - usage.cache_write_tokens, 0)
    return (
        uncached * price.input
        + usage.cache_read_tokens * price.cache_read
        + usage.cache_write_tokens * price.cache_write
        + usage.output_tokens * price.output
    ) / 1e6


def project_cost_usd(text_chars: int, max_output_tokens: int, price: Price | None) -> float:
    """Worst-case spend before a call: four characters per input token, full output budget."""
    if price is None:
        return 0.0
    return (text_chars / 4 * price.input + max_output_tokens * price.output) / 1e6
