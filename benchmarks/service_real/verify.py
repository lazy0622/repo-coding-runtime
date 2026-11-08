"""Independent behavior checks followed by unchanged upstream regression files."""

import json
import subprocess
import sys
from pathlib import Path


def behavior(case_id):
    if case_id.startswith("humanize-"):
        import humanize

        if case_id == "humanize-intcomma-large-int":
            for exponent in (350, 401, 700):
                for sign in (1, -1):
                    number = sign * (10**exponent + 54321)
                    assert humanize.intcomma(number) == format(number, ",")
            assert humanize.intcomma(1234567) == "1,234,567"
            assert humanize.intcomma(float("inf")) == "+Inf"
        elif case_id == "humanize-fractional-whole":
            for value, expected in [
                (0, "0"),
                (0.0, "0"),
                (0.9999999, "1"),
                (4.9999999, "5"),
                (-4.9999999, "-5"),
                (2.25, "2 1/4"),
                (-2.25, "-2 1/4"),
                (0.5, "1/2"),
            ]:
                assert humanize.fractional(value) == expected, value
        elif case_id == "humanize-naturalsize-rollover":
            for value, options, expected in [
                (999999, {}, "1.0 MB"),
                (-999999, {}, "-1.0 MB"),
                (999999999, {}, "1.0 GB"),
                (1024**2 - 1, {"binary": True}, "1.0 MiB"),
                (1024**2 - 1, {"gnu": True}, "1.0M"),
                (100000, {}, "100.0 kB"),
                (999999, {"format": "%.3f"}, "999.999 kB"),
            ]:
                assert humanize.naturalsize(value, **options) == expected, value
    elif case_id == "itsdangerous-future-timestamp":
        from itsdangerous import SignatureExpired, TimestampSigner

        class ClockSigner(TimestampSigner):
            clock = 2000000000

            def get_timestamp(self):
                return self.clock

        signer = ClockSigner("evaluation-key")
        token = signer.sign(b"payload")
        signer.clock -= 7
        try:
            signer.unsign(token, max_age=20)
        except SignatureExpired as error:
            assert error.payload == b"payload"
            assert error.date_signed is not None
        else:
            raise AssertionError("future timestamp was accepted")
        assert signer.unsign(token) == b"payload"
        signer.clock += 10
        assert signer.unsign(token, max_age=20) == b"payload"
        signer.clock += 30
        try:
            signer.unsign(token, max_age=20)
        except SignatureExpired:
            pass
        else:
            raise AssertionError("expired timestamp was accepted")
    elif case_id == "itsdangerous-none-salt":
        from itsdangerous import Signer

        for derivation in ("django-concat", "concat", "hmac", "none"):
            default = Signer("evaluation-key", key_derivation=derivation)
            optional = Signer("evaluation-key", salt=None, key_derivation=derivation)
            assert optional.sign(b"payload") == default.sign(b"payload")
            assert optional.unsign(default.sign(b"payload")) == b"payload"
        assert Signer("key", salt="custom").sign("value") == Signer(
            "key", salt=b"custom"
        ).sign("value")
    else:
        raise ValueError(case_id)


def main():
    case_id, workspace = sys.argv[1], Path(sys.argv[2]).resolve()
    cases = json.loads(Path(__file__).with_name("cases.json").read_text())["cases"]
    case = next(item for item in cases if item["id"] == case_id)
    sys.path.insert(0, str(workspace / "src"))
    behavior(case_id)
    print("Independent behavior checks passed", flush=True)
    # Run pytest in this interpreter so the trusted source path wins over any
    # installed library; test discovery files are unchanged from the base.
    for name in case["regression_files"]:
        before = subprocess.check_output(["git", "show", "HEAD:" + name], cwd=workspace)
        assert (workspace / name).read_bytes().replace(
            b"\r\n", b"\n"
        ) == before.replace(b"\r\n", b"\n"), "Upstream regression file was changed"
    import pytest

    raise SystemExit(pytest.main([*case["regression_files"], "-q", "-o", "addopts="]))


if __name__ == "__main__":
    main()
