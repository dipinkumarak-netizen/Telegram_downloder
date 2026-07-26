from app.downloader import retry_delay


def test_retry_delay_exponential_and_capped() -> None:
    assert retry_delay(1, 10) == 10
    assert retry_delay(2, 10) == 20
    assert retry_delay(20, 10) == 3600
