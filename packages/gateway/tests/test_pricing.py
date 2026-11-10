import pytest
from modelforge_gateway.pricing import cost_usd, project_cost_usd
from modelforge_gateway.profiles import Price
from modelforge_gateway.schemas import Usage


def test_cost_usd_opus_example() -> None:
    price = Price(input=4.0, output=20.0, cache_read=0.20, cache_write=5.0)
    usage = Usage(
        input_tokens=1000, output_tokens=200, cache_read_tokens=400, cache_write_tokens=100
    )
    assert cost_usd(usage, price) == pytest.approx(0.00658)
    assert cost_usd(usage, None) == 0.0
    assert project_cost_usd(4000, 1000, price) == pytest.approx((1000 * 4.0 + 1000 * 20.0) / 1e6)


def test_cost_usd_clamps_uncached_input_at_zero() -> None:
    price = Price(input=4.0, output=20.0, cache_read=0.20, cache_write=5.0)
    usage = Usage(input_tokens=100, output_tokens=0, cache_read_tokens=500, cache_write_tokens=0)
    assert cost_usd(usage, price) == pytest.approx(500 * 0.20 / 1e6)


def test_project_cost_usd_without_price_is_zero() -> None:
    assert project_cost_usd(4000, 1000, None) == 0.0
