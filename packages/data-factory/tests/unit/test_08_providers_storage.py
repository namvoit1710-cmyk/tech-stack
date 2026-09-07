import pytest
import requests
from unittest.mock import MagicMock, patch
from app.layer4_frameworks.providers.storage.node_storage_provider import NodeFileStorageProvider
from app.layer2_application.interfaces.storage_interface import FileVersionConflictError

@pytest.mark.unit
class TestNodeFileStorageProvider:
    def setup_method(self):
        self.logger_mock = MagicMock()
        self.provider = NodeFileStorageProvider("http://test-server.com", self.logger_mock)

    @pytest.fixture(autouse=True)
    def _disable_presigned_upload(self):
        # The presigned large-file upload path reads os.path.getsize on a real
        # file; these unit tests exercise the standard upload path with mocked IO.
        with patch(
            'app.layer4_frameworks.providers.storage.node_storage_provider.settings.FILE_SERVICE_USE_PRESIGNED_UPLOAD_FOR_LARGE',
            False,
        ):
            yield

    def test_init(self):
        """Test provider initialization"""
        assert self.provider.node_url == "http://test-server.com/api/v1"
        assert self.provider.logger == self.logger_mock

    def test_generate_presigned_url_with_http(self):
        """Test generating presigned URL for HTTP URLs"""
        result = self.provider.generate_presigned_url("http://example.com/file.csv")
        assert result == "http://example.com/file.csv"

    @patch('os.path.exists', return_value=True)
    def test_generate_presigned_url_with_local_file(self, mock_exists):
        """Test generating presigned URL for local file paths"""
        result = self.provider.generate_presigned_url("D:/sample.xlsx")
        assert result == "D:/sample.xlsx"

    @patch('requests.get')
    def test_generate_presigned_url_success(self, mock_get):
        """Test latest-version download URL generation"""
        result = self.provider.generate_presigned_url("test.csv")
        assert result == "http://test-server.com/api/v1/download/test.csv"
        mock_get.assert_not_called()

    @patch('requests.get')
    def test_generate_presigned_url_failure(self, mock_get):
        """Test specific-version download URL generation"""
        result = self.provider.generate_presigned_url("test.csv")
        assert result == "http://test-server.com/api/v1/download/test.csv"
        self.logger_mock.error.assert_not_called()

    def test_generate_presigned_url_for_specific_version(self):
        result = self.provider.generate_presigned_url("file-1", version_id="ver-2")
        assert result == "http://test-server.com/api/v1/files/versions/ver-2/download"

    @patch('requests.post')
    def test_resolve_download_url_uses_presigned_for_large_files(self, mock_post):
        presigned_response = MagicMock()
        presigned_response.raise_for_status.return_value = None
        presigned_response.json.return_value = {"url": "https://presigned.example.com/file"}
        mock_post.return_value = presigned_response

        with patch('app.layer4_frameworks.providers.storage.node_storage_provider.settings.FILE_SERVICE_USE_PRESIGNED_DOWNLOAD_FOR_LARGE', True), \
             patch('app.layer4_frameworks.providers.storage.node_storage_provider.settings.FILE_SERVICE_PRESIGNED_DOWNLOAD_EXPIRES_SEC', 3600):
            result = self.provider._resolve_download_url("large-file.csv")

        assert result == "https://presigned.example.com/file"
        assert mock_post.call_count == 1

    @patch('requests.post')
    def test_resolve_download_url_falls_back_when_presigned_not_available(self, mock_post):
        presigned_response = MagicMock()
        presigned_response.raise_for_status.side_effect = requests.exceptions.HTTPError("not supported")
        mock_post.return_value = presigned_response

        with patch('app.layer4_frameworks.providers.storage.node_storage_provider.settings.FILE_SERVICE_USE_PRESIGNED_DOWNLOAD_FOR_LARGE', True):
            result = self.provider._resolve_download_url("large-file.csv")

        assert result == "http://test-server.com/api/v1/download/large-file.csv"

    @patch('tempfile.NamedTemporaryFile')
    @patch('requests.get')
    @patch('builtins.open')
    def test_download_and_read_csv(self, mock_open, mock_get, mock_tempfile):
        """Test downloading and reading CSV file"""
        # Setup mocks
        mock_file_obj = MagicMock()
        mock_tempfile.return_value.__enter__.return_value = mock_file_obj
        mock_tempfile.return_value.__enter__.return_value.name = "/tmp/test.csv"

        mock_response = MagicMock()
        mock_response.__enter__.return_value = mock_response
        mock_response.headers = {}  # content-length defaults to 0 -> simple (non-parallel) download
        mock_response.raw.read.return_value = b""  # stop shutil.copyfileobj immediately
        mock_response.iter_content.return_value = [b"data"]
        mock_get.return_value = mock_response

        # Mock polars; disable presigned download so no real network call is made.
        # CSV reading now lives in the injectable CsvSourceReader strategy.
        with patch('app.layer4_frameworks.providers.storage.node_storage_provider.settings.FILE_SERVICE_USE_PRESIGNED_DOWNLOAD_FOR_LARGE', False), \
             patch('app.layer4_frameworks.providers.formats.csv_reader.pl.read_csv') as mock_read_csv:
            mock_df = MagicMock()
            mock_df.columns = ["col1", "col2"]
            mock_read_csv.return_value = mock_df

            result = self.provider.download_and_read("test.csv", "csv")

            assert result is not None
            mock_read_csv.assert_called_once()

    @patch('app.layer4_frameworks.providers.storage.node_storage_provider._BACKOFF_SLEEP')
    @patch('tempfile.NamedTemporaryFile')
    @patch('requests.get')
    @patch('builtins.open')
    @patch.object(NodeFileStorageProvider, '_read_dataframe')
    def test_download_and_read_uses_configured_timeout_and_retry_backoff(
        self,
        mock_read_dataframe,
        mock_open,
        mock_get,
        mock_tempfile,
        mock_backoff_sleep,
    ):
        mock_file_obj = MagicMock()
        mock_tempfile.return_value.__enter__.return_value = mock_file_obj
        mock_tempfile.return_value.__enter__.return_value.name = "/tmp/test.csv"

        mock_response = MagicMock()
        mock_response.__enter__.return_value = mock_response
        mock_response.headers = {}  # content-length 0 -> simple download branch
        mock_response.raw.read.return_value = b""
        mock_response.iter_content.return_value = [b"data"]
        mock_response.raise_for_status.return_value = None

        # First attempt times out; second attempt performs a content-length probe
        # request followed by the actual download request (both configured-timeout).
        mock_get.side_effect = [
            requests.exceptions.ReadTimeout("read timeout"),
            mock_response,
            mock_response,
        ]
        mock_read_dataframe.return_value = MagicMock()

        with patch('app.layer4_frameworks.providers.storage.node_storage_provider.settings.FILE_SERVICE_CONNECT_TIMEOUT_SEC', 7), \
             patch('app.layer4_frameworks.providers.storage.node_storage_provider.settings.FILE_SERVICE_READ_TIMEOUT_SEC', 123), \
             patch('app.layer4_frameworks.providers.storage.node_storage_provider.settings.FILE_SERVICE_MAX_RETRIES', 2), \
                             patch('app.layer4_frameworks.providers.storage.node_storage_provider.settings.FILE_SERVICE_USE_PRESIGNED_DOWNLOAD_FOR_LARGE', False), \
               patch('app.layer4_frameworks.providers.storage.node_storage_provider.settings.FILE_SERVICE_RETRY_BACKOFF_SEC', 2.0), \
               patch('app.layer4_frameworks.providers.storage.node_storage_provider.settings.FILE_SERVICE_FAIL_FAST_ON_TIMEOUT', False):
            self.provider.download_and_read("test.csv", "csv")

        assert mock_get.call_count == 3
        for call in mock_get.call_args_list:
            assert call.kwargs["timeout"] == (7, 123)
        mock_backoff_sleep.assert_called_once_with(2.0)

    @patch('os.path.exists', return_value=True)
    @patch.object(NodeFileStorageProvider, '_read_dataframe')
    def test_download_and_read_local_xlsx(self, mock_read_dataframe, mock_exists):
        """Test reading local xlsx files without downloading"""
        mock_df = MagicMock()
        mock_read_dataframe.return_value = mock_df

        result = self.provider.download_and_read("D:/sample.xlsx", ".xlsx")

        assert result is mock_df
        mock_read_dataframe.assert_called_once_with(
            "D:/sample.xlsx",
            "xlsx",
            sheet_names=None,
            merge_sheets=False,
            add_sheet_name_column=False,
            header_row=None,
        )

    @patch('app.layer4_frameworks.providers.formats.excel_reader.pl.read_excel')
    def test_read_dataframe_xlsx(self, mock_read_excel):
        """Test reading xlsx dataframes directly (delegates to the Excel reader strategy)"""
        mock_df = MagicMock()
        mock_df.columns = [" id ", " name "]
        mock_read_excel.return_value = mock_df

        result = self.provider._read_dataframe("D:/sample.xlsx", "xlsx")

        assert result is mock_df
        assert result.columns == ["id", "name"]
        mock_read_excel.assert_called_once_with("D:/sample.xlsx")

    @patch('requests.post')
    @patch('builtins.open')
    def test_upload_file_success(self, mock_open, mock_post):
        """Test successful file upload"""
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {
            "file_id": "file-123",
            "version_id": "ver-456",
            "filename": "file.csv",
            "status": "ready",
        }
        mock_post.return_value = mock_response

        result = self.provider.upload_file("/path/to/file.csv", "csv")

        assert result.file_id == "file-123"
        assert result.version_id == "ver-456"
        assert result.download_url == "http://test-server.com/api/v1/files/versions/ver-456/download"
        self.logger_mock.info.assert_called()

    @patch('requests.post')
    @patch('builtins.open')
    def test_upload_file_uses_configured_timeouts(self, mock_open, mock_post):
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {
            "file_id": "file-123",
            "version_id": "ver-456",
            "filename": "file.csv",
            "status": "ready",
        }
        mock_post.return_value = mock_response

        with patch('app.layer4_frameworks.providers.storage.node_storage_provider.settings.FILE_SERVICE_CONNECT_TIMEOUT_SEC', 9), \
             patch('app.layer4_frameworks.providers.storage.node_storage_provider.settings.FILE_SERVICE_UPLOAD_TIMEOUT_SEC', 321):
            self.provider.upload_file("/path/to/file.csv", "csv")

        assert mock_post.call_args.kwargs["timeout"] == (9, 321)

    @patch('requests.post')
    @patch('builtins.open')
    @patch.object(NodeFileStorageProvider, '_get_file_metadata')
    def test_upload_file_uses_edited_source_filename_for_versioned_updates(
        self,
        mock_get_file_metadata,
        mock_open,
        mock_post,
    ):
        mock_get_file_metadata.return_value = {
            "filename": "orders.csv",
        }
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {
            "file_id": "file-123",
            "version_id": "ver-456",
            "filename": "orders_edited_by_data-factory.csv",
            "status": "ready",
        }
        mock_post.return_value = mock_response

        self.provider.upload_file(
            "/tmp/tmpabc.csv",
            "csv",
            file_id="file-123",
            version_id="ver-123",
        )

        assert mock_post.call_args.kwargs["files"]["file"][0] == "orders_edited_by_data-factory.csv"
        assert mock_post.call_args.kwargs["params"] == {
            "file_id": "file-123",
            "previous_version_id": "ver-123",
        }

    @patch('requests.post')
    @patch('builtins.open')
    def test_upload_file_uses_explicit_filename_when_provided(self, mock_open, mock_post):
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {
            "file_id": "file-123",
            "version_id": "ver-456",
            "filename": "orders_validation_result_by_data-factory.csv",
            "status": "ready",
        }
        mock_post.return_value = mock_response

        self.provider.upload_file(
            "/tmp/tmpabc.csv",
            "csv",
            filename="orders_validation_result_by_data-factory.csv",
        )

        assert mock_post.call_args.kwargs["files"]["file"][0] == "orders_validation_result_by_data-factory.csv"
        assert mock_post.call_args.kwargs["params"] == {}

    @patch.object(NodeFileStorageProvider, '_get_current_version_id', return_value="ver-new")
    @patch('requests.post')
    @patch('builtins.open')
    def test_upload_file_raises_stale_version_error(self, mock_open, mock_post, mock_get_current_version_id):
        mock_response = MagicMock()
        mock_response.status_code = 409
        mock_post.return_value = mock_response

        with pytest.raises(FileVersionConflictError) as exc_info:
            self.provider.upload_file(
                "/path/to/file.csv",
                "csv",
                file_id="file-123",
                version_id="ver-old",
            )

        assert exc_info.value.current_version_id == "ver-new"
        mock_get_current_version_id.assert_called_once_with("file-123")

    @patch.object(NodeFileStorageProvider, '_get_current_version_id', return_value="ver-new")
    @patch('requests.post')
    @patch('builtins.open')
    def test_upload_file_treats_stale_400_as_version_conflict(
        self,
        mock_open,
        mock_post,
        mock_get_current_version_id,
    ):
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_post.return_value = mock_response

        with pytest.raises(FileVersionConflictError) as exc_info:
            self.provider.upload_file(
                "/path/to/file.csv",
                "csv",
                file_id="file-123",
                version_id="ver-old",
            )

        assert exc_info.value.current_version_id == "ver-new"
        mock_get_current_version_id.assert_called_once_with("file-123")

    @patch('tempfile.NamedTemporaryFile')
    def test_upload_dataframe_csv(self, mock_tempfile):
        """Test uploading dataframe as CSV"""
        mock_file_obj = MagicMock()
        mock_tempfile.return_value.__enter__.return_value = mock_file_obj
        mock_tempfile.return_value.__enter__.return_value.name = "/tmp/test.csv"

        df_mock = MagicMock()

        with patch.object(self.provider, 'upload_file') as mock_upload:
            mock_upload.return_value = MagicMock(download_url="http://uploaded.com/result.csv")

            result = self.provider.upload_dataframe(df_mock, "csv")

            assert result.download_url == "http://uploaded.com/result.csv"
            df_mock.write_csv.assert_called_once_with("/tmp/test.csv")
