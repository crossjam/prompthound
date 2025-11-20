import warnings
import pytest

def test_import_slugify_no_warning():
    """
    Test that importing the vendored slugify library does not produce any SyntaxWarning.
    """
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        from prompthound.vendor import slugify
        assert len(w) == 0, f"Importing slugify produced unexpected warnings: {w}"
