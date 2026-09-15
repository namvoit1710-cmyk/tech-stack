from app.layer1_domain.value_objects.wait_type import WaitType
from app.layer1_domain.value_objects.wait_status import WaitStatus


class TestWaitType:
    def test_values(self):
        assert WaitType.FIXED_DURATION == "fixed_duration"
        assert WaitType.UNTIL_DATETIME == "until_datetime"
        assert WaitType.MANUAL_APPROVAL == "manual_approval"

    def test_is_string(self):
        assert isinstance(WaitType.FIXED_DURATION, str)


class TestWaitStatus:
    def test_values(self):
        assert WaitStatus.COMPLETED == "completed"
        assert WaitStatus.TIMED_OUT == "timed_out"
        assert WaitStatus.CANCELLED == "cancelled"
        assert WaitStatus.ERROR == "error"
