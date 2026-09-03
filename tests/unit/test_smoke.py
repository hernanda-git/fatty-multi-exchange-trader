def test_package_exposes_version() -> None:
    from fatty_trader import __version__

    assert __version__ == "0.1.0"
