import pytest

from gradle import GradleError, GradleRunner, parse_build_result


def test_parse_build_successful():
    result = parse_build_result("...\nBUILD SUCCESSFUL in 42s\n")
    assert result["success"] is True
    assert result["failures"] == []
    assert result["duration"] == "42s"


def test_parse_build_failed_lists_tests():
    log = """
    com.example.AddNewAddressTest > addAddress FAILED
        java.lang.AssertionError: expected true
    BUILD FAILED in 1m
    """
    result = parse_build_result(log)
    assert result["success"] is False
    assert result["failures"] == [
        {"class": "com.example.AddNewAddressTest", "test": "AddNewAddressTest",
         "method": "addAddress"}
    ]


def test_missing_gradlew_raises(tmp_path):
    with pytest.raises(GradleError):
        GradleRunner(str(tmp_path)).build_command("assembleDebug")


def test_mode_is_fake_without_gradlew(tmp_path):
    assert GradleRunner(str(tmp_path)).mode() == "fake"


def test_mode_is_live_with_gradlew(tmp_path):
    (tmp_path / "gradlew.bat").write_text("", encoding="utf-8")
    assert GradleRunner(str(tmp_path)).mode() == "live"


def test_connected_test_command_includes_filter(tmp_path):
    (tmp_path / "gradlew.bat").write_text("", encoding="utf-8")
    cmd = GradleRunner(str(tmp_path)).connected_test_command(
        "ru.leroymerlin.mobile.AddNewAddressTest"
    )
    joined = " ".join(cmd)
    assert ":android:app:connectedDebugAndroidTest" in joined
    assert "-Pandroid.testInstrumentationRunnerArguments.class=ru.leroymerlin.mobile.AddNewAddressTest" in joined
