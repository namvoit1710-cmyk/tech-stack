import pytest
from unittest.mock import MagicMock, patch
from pydantic_settings import SettingsConfigDict
from app.layer4_frameworks.config.app_config import Settings, settings
from app.layer4_frameworks.logger.app_logger import AppLogger

@pytest.mark.unit
class TestAppConfig:
    def test_settings_defaults(self):
        """Test default settings values"""
        with patch.dict('os.environ', {}, clear=True), \
             patch('app.layer4_frameworks.config.app_config.Settings.model_config', \
                   SettingsConfigDict(env_file=None, extra="ignore")):
            test_settings = Settings(PORT=8000)
            assert test_settings.APP_NAME == "Clean Data Factory"
            assert test_settings.APP_MODE == "REST"
            assert test_settings.PORT == 8000
            assert "sqlite" in test_settings.DATABASE_URL
            assert test_settings.DB_ECHO is False
            assert test_settings.FILE_SERVER_URL == "http://localhost:5000"
            assert test_settings.FILE_SERVICE_USE_PRESIGNED_DOWNLOAD_FOR_LARGE is True
            assert test_settings.FILE_SERVICE_PRESIGNED_DOWNLOAD_MIN_SIZE_MB == 25
            assert test_settings.FILE_SERVICE_PRESIGNED_DOWNLOAD_EXPIRES_SEC == 3600
            assert test_settings.MEMORY_GUARD_ENABLED is True
            assert test_settings.MEMORY_GUARD_MIN_AVAILABLE_PERCENT == 5.0
            assert test_settings.ADAPTIVE_BATCHING_ENABLED is True
            assert test_settings.ADAPTIVE_BATCH_RUNTIME_SHRINK_ENABLED is True
            assert test_settings.ADAPTIVE_BATCH_ALLOW_GROWTH is False
            assert test_settings.ADAPTIVE_BATCH_REEVALUATE_EVERY_N_BATCHES == 1
            assert test_settings.TRANSFORM_LAZY_CSV_FAST_PATH_ENABLED is False
            assert test_settings.TRANSFORM_LAZY_CSV_SINK_ENABLED is True
            assert test_settings.BATCH_SIZE == 500000
            assert test_settings.BATCH_SIZE_MIN == 100000
            assert test_settings.BATCH_SIZE_MAX == 2000000
            assert test_settings.BATCH_MEMORY_FRACTION == 0.1
            assert test_settings.MAX_FILE_SIZE == 100

    def test_settings_with_env_vars(self):
        """Test settings with environment variables"""
        with patch.dict('os.environ', {
            'APP_MODE': 'TEST',
            'PORT': '9000',
            'DATABASE_URL': 'postgresql://test',
            'DB_ECHO': 'true',
            'MEMORY_GUARD_MIN_AVAILABLE_PERCENT': '7.5',
            'FILE_SERVICE_PRESIGNED_DOWNLOAD_MIN_SIZE_MB': '80',
            'ADAPTIVE_BATCH_REEVALUATE_EVERY_N_BATCHES': '3',
            'TRANSFORM_LAZY_CSV_FAST_PATH_ENABLED': 'true',
            'BATCH_SIZE': '1000',
            'BATCH_SIZE_MAX': '1500'
        }):
            test_settings = Settings()
            assert test_settings.APP_MODE == "TEST"
            assert test_settings.PORT == 9000
            assert test_settings.DATABASE_URL == "postgresql://test"
            assert test_settings.DB_ECHO is True
            assert test_settings.MEMORY_GUARD_MIN_AVAILABLE_PERCENT == 7.5
            assert test_settings.FILE_SERVICE_PRESIGNED_DOWNLOAD_MIN_SIZE_MB == 80
            assert test_settings.ADAPTIVE_BATCH_REEVALUATE_EVERY_N_BATCHES == 3
            assert test_settings.TRANSFORM_LAZY_CSV_FAST_PATH_ENABLED is True
            assert test_settings.BATCH_SIZE == 1000
            assert test_settings.BATCH_SIZE_MAX == 1500

    def test_global_settings_instance(self):
        """Test that global settings instance exists"""
        assert settings is not None
        assert isinstance(settings, Settings)

@pytest.mark.unit
class TestAppLogger:
    def setup_method(self):
        self.logger = AppLogger()

    def test_logger_initialization(self):
        """Test logger initialization"""
        assert self.logger is not None
        assert hasattr(self.logger, 'logger')

    def test_info_logging(self):
        """Test info level logging"""
        with patch('logging.Logger.info') as mock_info:
            self.logger.info("Test message")
            mock_info.assert_called_once_with("Test message ")

    def test_info_logging_with_kwargs(self):
        """Test info logging with additional kwargs"""
        with patch('logging.Logger.info') as mock_info:
            self.logger.info("Test message", key="value")
            mock_info.assert_called_once_with("Test message {'key': 'value'}")

    def test_error_logging(self):
        """Test error level logging"""
        with patch('logging.Logger.error') as mock_error:
            self.logger.error("Error message")
            mock_error.assert_called_once_with("Error message ")

    def test_debug_logging(self):
        """Test debug level logging"""
        with patch('logging.Logger.debug') as mock_debug:
            self.logger.debug("Debug message")
            mock_debug.assert_called_once_with("Debug message ")

    def test_warning_logging(self):
        """Test warning level logging"""
        with patch('logging.Logger.warning') as mock_warning:
            self.logger.warning("Warning message")
            mock_warning.assert_called_once_with("Warning message ")